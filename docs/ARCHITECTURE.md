# 아키텍처

데이터가 어디서 와서 어떤 스크립트를 거쳐 무엇이 되는지, 그리고 무엇을 바꾸면 무엇이 다시 돌아야 하는지를 적는다.

---

## 1. 저장소 밖 데이터 위치

| 위치 | 내용 | 접근 |
|---|---|---|
| `V:\data\` (= `\\192.168.0.26\문화재청_하수범김동현\모델링\data`) | 기상·위성·지형 래스터, 공통 마스크, 산불 레퍼런스, LightGBM fold 모델 | **읽기 전용** |
| `C:\for_sgis\data\fire_reference\` | 위 NAS의 산불 레퍼런스 스냅샷 (2026-09-01 15:07) | 작업 사본 |
| `C:\for_sgis\data\grid_data\` | SGIS 자료제공 다운로드 원본 + `derived/` 산출물 | 로컬 |
| `C:\for_sgis\models\` | 학습된 GRU 가중치·스케일러 | 로컬 |

> **NAS는 절대 쓰지 않는다.** 가공이 필요하면 작업 폴더로 복사해서 처리한다.
> `fire_reference/`는 그래서 사본이며, 다른 세션이 NAS 원본을 재생성하는 중에도 안전하게 읽을 수 있다.

---

## 2. 공통 격자 — 모든 것의 기준

```
common_mask_500m_5179.tif
  CRS      EPSG:5179 (Korea 2000 Unified, UTM-K)
  크기     2130 행 × 2123 열
  해상도   500m
  원점     x = 414341.4294,  y = 2269364.0712
  유효셀   403,385개 (mask == 1)
```

기존 연구의 모든 피처 래스터가 이 마스크에 클립·정렬돼 있다. **격자는 바꿀 수 없다.**
외부 데이터(SGIS 등)는 전부 이쪽으로 끌어온다.

픽셀 인덱스 `(prow, pcol)` ↔ 좌표 변환:

```
x = 414341.4294 + 500 × (pcol + 0.5)
y = 2269364.0712 − 500 × (prow + 0.5)
```

---

## 3. 파이프라인 — 스크립트 의존관계

```mermaid
flowchart LR
    subgraph L["라벨"]
        S40["40_build_fire_cell_index<br/>화재 셀-시간"]
        S40B["40b_rasterize_rule_compare<br/>규칙 검증"]
    end
    subgraph G["지오코딩 복구"]
        S30["30 → 30b → 30c<br/>SGIS 지오코딩"]
    end
    subgraph M["모델"]
        S20["20_label_audit"]
        S21["21_build_preignition_markers"]
        S23["23_merge_ignition_dataset"]
        S24["24_train_gru_ignition_5fold"]
        S25["25_hardneg_ratio_sweep"]
    end
    subgraph E["SGIS 결합"]
        S22["22_sgis_grid_weights"]
        S22B["22b_verify_sgis_grid_origin"]
        S26["26_build_mask_to_sgis_lookup"]
        S28["28_build_gridcd_lookup"]
        S29["29_build_exposure_layer"]
    end
    subgraph D["의사결정"]
        S32["32_fullgrid_inference"]
        S34["34_priority_final"]
        S35["35_case_replay"]
        S51["51_daily_scan_full_period"]
    end
    subgraph W["웹 자산"]
        S53["53_build_multiday_assets"]
        S54["54_time_axis_risk"]
        S55["55_build_timeline_index"]
        S64["64_build_adm_boundaries"]
    end
    subgraph X["공유 모듈"]
        SM["_stage2_model  모델 로드"]
        SE["_exposure  노출 계산"]
    end

    S30 --> S40
    S30 --> S21
    S20 --> S21 --> S23 --> S24
    S23 --> S25
    S22 --> S26 --> S29
    S22B -.검증.-> S22
    S28 --> S29
    S24 --> S32 --> S34
    S29 --> S34
    S24 --> S35
    S29 --> S35
    S40 --> S35
    SM --> S32
    SM --> S35
    SM --> S51
    SE --> S35
    SE --> S51
    S65["65_fetch_sgis_vulnerability"] --> SE
    S29 --> SE
    S41["41_cell_admin_lookup"] --> SE
    S35 --> S53
    S51 --> S54 --> S55
    S51 --> S55
    S64 --> S55
```

### 실행 순서 (처음부터 재구축할 때)

```bash
# ① SGIS 지오코딩 복구 — 다른 단계의 입력이 되므로 먼저
python scripts/30_recover_failed_geocodes.py
python scripts/30b_recover_remaining.py
python scripts/30c_recover_sigu.py

# ② 격자 정합 (SGIS 데이터 수령 후)
python scripts/22_sgis_grid_weights.py
python scripts/22b_verify_sgis_grid_origin.py     # 검증만, 산출물 없음
python scripts/26_build_mask_to_sgis_lookup.py
python scripts/28_build_gridcd_lookup.py
python scripts/29_build_exposure_layer.py

# ③ 화재 라벨
python scripts/40_build_fire_cell_index.py

# ④ 모델 (학습셋은 별도 준비 중)
python scripts/24_train_gru_ignition_5fold.py

# ⑤ SGIS 인구 특성 · 행정동 경계 (노출 계산의 입력)
python scripts/41_build_cell_admin_lookup.py
python scripts/64_build_adm_boundaries.py
python scripts/65_fetch_sgis_vulnerability.py

# ⑥ 추론 · 의사결정
$env:TARGET_DT='2025-03-22 12:00'; python scripts/32_fullgrid_inference_ignition.py
$env:STAMP='20250322_1200';        python scripts/34_priority_final.py
$env:DATE='2025-03-22';            python scripts/35_case_replay.py

# ⑦ 전 기간 스캔 — 약 12시간. 마지막은 반드시 접미사 없이 돌려야
#    daily_scan_all.csv 가 세 시각을 다 합쳐 갱신된다.
$env:FORCE='1'; $env:SCAN_HOURS='10'; $env:OUT_SUFFIX='_h10'; python scripts/51_daily_scan_full_period.py
$env:FORCE='1'; $env:SCAN_HOURS='8';  $env:OUT_SUFFIX='_h08'; python scripts/51_daily_scan_full_period.py
$env:FORCE='1'; $env:SCAN_HOURS='11,14'; $env:OUT_SUFFIX='';  python scripts/51_daily_scan_full_period.py

# ⑧ 웹 자산
python scripts/52_select_case_days.py
python scripts/53_build_multiday_assets.py
python scripts/54_time_axis_risk.py
python scripts/55_build_timeline_index.py
```

UI 를 눈으로 검증할 때는 `npm run dev` 대신 **프로덕션 빌드**를 쓴다.
이 환경의 dev 서버는 하이드레이션에 실패해 "데이터 불러오는 중"에서 멈춘다
(코드 문제가 아니라 HMR 문제로, 변경을 되돌려도 재현된다).

```bash
cd web && npx next build && npx next start -p 3100
```

---

## 4. 스크립트별 입출력

| # | 스크립트 | 입력 | 출력 |
|---|---|---|---|
| 20 | `label_audit_new_ignition` | `seq_dataset_12h_multih_4v1.parquet` (NAS) | `label_audit_new_ignition.csv` |
| 21 | `build_preignition_markers` | NAS 래스터 전체 · 산불 이력 | `preignition_markers_raw.parquet` |
| 22 | `sgis_grid_weights` | 공통 마스크 | `sgis_grid_weights.json` |
| 22b | `verify_sgis_grid_origin` | SGIS 격자경계 API | (검증 출력만) |
| 23 | `merge_ignition_dataset` | 21번 산출 + LightGBM fold 모델 | `seq_dataset_ignition_multih.parquet` |
| 24 | `train_gru_ignition_5fold` | 23번 산출 | `models/gru_ign_*` · `gru_ignition_multih_{results,probs}.csv` |
| 25 | `hardneg_ratio_sweep` | 23번 산출 | `gru_ignition_hardneg_sweep.csv` |
| 26 | `build_mask_to_sgis_lookup` | 공통 마스크 | `mask_to_sgis_500m.parquet` (1,613,540행) |
| 28 | `build_gridcd_lookup` | SGIS 격자경계 SHP 30도엽 | `sgis_gridcd_500m.parquet` (418,728셀) |
| 29 | `build_exposure_layer` | SGIS 격자통계 CSV + 26 + 28 | `mask_exposure_500m.parquet` |
| 30/b/c | `recover_*` | 산불 이력 + SGIS 지오코딩 API | `fire_events_geocode_recovered.csv` |
| 32 | `fullgrid_inference_ignition` | NAS 래스터 + GRU 모델 | `hazard_ignition_{stamp}.parquet` |
| 34 | `priority_final` | 32 + 29 + 토지피복 | `priority_final_{stamp}.parquet` · Top-N · 민감도 |
| 35 | `case_replay` | 32와 동일 + 29 + 40 | `replay_{date}_{grid,summary,top}` |
| 40 | `build_fire_cell_index` | dNBR 폴리곤 + 산불 이력 + 30번 복구 | `fire_cells` · `fire_cell_hours` · 거리필터 로그 |
| 40b | `rasterize_rule_compare` | dNBR 폴리곤 | `rasterize_rule_compare.csv` |
| 41 | `build_cell_admin_lookup` | SGIS 격자경계 + 공통 마스크 | `cell_admin.parquet` (격자→행정동) |
| 50 | `build_web_assets` | 35번 산출 | 단일 사례일 웹 자산 |
| 51 | `daily_scan_full_period` | `_stage2_model` + `_exposure` + NAS | `daily_scan_{YYYY}{접미사}.parquet` · `daily_scan_all.csv` · `ignition_ranks.csv` |
| 52 | `select_case_days` | 51번 산출 | `case_days.json` (사례일 25일 선정) |
| 53 | `build_multiday_assets` | 35번 replay 25일치 | `web/public/data/d/{YMD}/*` |
| 54 | `time_axis_risk` | `daily_scan_all.csv` | `time_risk.json` (시간축 위험등급) |
| 55 | `build_timeline_index` | `daily_scan_all.csv` + 54 + 40 | `timeline.json` (737일 슬라이더) |
| 60 | `verify_cnn_v4` | 교수님 공간추론 CSV | CNN 구조 확정 (padding=1, 오차 6.17e-07) |
| 61 | `validate_cnn_v4b_folds` | v4b 가중치 5-fold | 폴드별 AUROC 재현 검증 |
| 62 | `train_cnn_v4_ratio` | seq 데이터셋 | `models_v4/{arch}_v4b_r20_s{비율}_*` |
| 63 | `compare_stage2_ab` | 51번 `ignition_ranks_*` 두 벌 | 모델 A/B 운영지표 비교 |
| 64 | `build_adm_boundaries` | SGIS 행정동 경계 SHP | `adm_dong.geojson` · `adm_index.json` |
| 65 | `fetch_sgis_vulnerability` | SGIS 통계 API | `sgis_dong_vulnerability.parquet` |
| — | `_stage2_model.py` | — | Stage1 LGBM + Stage2 로드 (32·35·51 공유) |
| — | `_exposure.py` | 29 + 41 + 65 | 격자별 노출 (35·51 공유) |

---

## 5. 모델 구조

```
Stage 1 — LightGBM (기존 연구 산출물, V:\data\ml_results\...\lgbm_models\)
  입력  23피처 (지형 5 · 토지피복 6 · 인문 4 · 기상 4 · 식생 2 · 계절 2)
  출력  P_lgbm — 해당 시점 공간 취약도
  누수  연도별 OOF — 그 해를 학습에 쓰지 않은 fold 모델이 그 해를 예측

Stage 2 — GRU (본 저장소, 배포 기본값 STAGE2_ARCH=gru_old)
  시계열  VPD · 풍속 × 12시간            shape (batch, 12, 2)
  정적    P_lgbm · NDVI · NDMI · hum4d · prcp4d · doy_sin · doy_cos   (7)
  인코더  nn.GRU(2 → 64, num_layers=2, dropout=0.3)
  헤드    Linear(64+7 → 64) → ReLU → Dropout(0.3) → Linear(64 → 3) → Sigmoid
  출력    t+1h · t+2h · t+3h 동시
  학습    1:10 재표본화 · Adam lr=3e-4 · batch 2048 · 최대 100 epoch · patience 15
  검증    연도별 leave-one-year-out 5-fold, train의 random 20%를 val
```

**v4b CNN 은 검증했으나 채택하지 않았다.** 검증셋 AUROC 는 0.884로 GRU(0.837)보다
높은데, 전국 격자에서 실제 발화를 상위 1% 안에 넣는 비율은 5.3% → 2.9%로 떨어졌다.
AUROC 는 40만 격자 순위 전체의 평균적 분리도이고 화면이 쓰는 "우선대응 상위 1%"는
꼬리 4,034셀의 순서만 본다. 서로 다른 것을 잰다. 근거와 원인 분리는
[STAGE2_ABLATION.md](STAGE2_ABLATION.md).

```
STAGE2_ARCH = gru_old   models/gru_ign_*        (기본, 배포)
              cnn       models_v4/cnn_v4b_*     (검증 완료, 미채택)
              gru       models_v4/gru_v4b_*     (원인 분리용 통제군)
```

---

## 5b. 노출 — 주간인구 보정

우선순위 산식의 노출항은 **상주인구가 아니라 주간 보정 인구**다.

```
day_idx  = (종사자 + 65세이상 + 15세미만) / 상주인구      전국 중앙값 0.77
pop_day  = pop_total x day_idx
expo_rank = pop_day 의 백분위
```

산불은 오후에 몰려 스캔 시각을 11·14시로 잡아 뒀는데 상주인구는 야간 기준이라,
낮에 예측하고 밤 인구로 피해를 셈하는 상태였다. 사례일 25일 Top10 3,250건 중
**56%가 교체**됐고 선정 격자의 주간인구는 상주인구의 1.39배다.

SGIS 에 주간인구·통근 통계가 없어 종사자수로 근사한 추정치다.
**농작업은 사업체 등록에 안 잡혀 농촌을 과소추정한다.** 행정동 지수를 격자에 곱하므로
동 내 균일 가정도 들어간다. 화면과 챗봇 프롬프트에 이 한계를 명시해 두었다.

고령·노후주택은 산식에 넣지 않는다. 거주 격자끼리 비교하면 발화지의 고령비율 0.91배,
노후주택비율 0.89배로 상관이 없다. "같은 위험도일 때 무엇을 잃는가"를 보여주는
표시용으로만 쓴다. `EXPO_MODE=resident` 로 보정 전 동작으로 돌아갈 수 있다.

**시각 규약** (반드시 지킬 것)

```
vpd_t0      = TARGET_DT − 1h  래스터
vpd_tm{k}   = TARGET_DT − (k+1)h  래스터        k = 1..11
예측 대상   = TARGET_DT + 1h / +2h / +3h
```

---

## 6. 무엇을 바꾸면 무엇이 다시 도는가

| 바뀐 것 | 다시 돌려야 하는 것 |
|---|---|
| 산불 이력 · 지오코딩 | 40 → (학습셋) → 24 → 32 → 34/35 |
| 라벨 규칙 (거리필터·24h·래스터화) | 40 → (학습셋) → 24 → 32 → 34/35 |
| SGIS 격자통계 갱신 | 29 → 34/35 → 51 → 53/54/55 |
| SGIS 인구 특성 (65번) | `_exposure` → 35 → 53, 51 → 54/55 |
| 공통 마스크 | **전부** (22 · 26 · 28 · 29 · 40 · 41 · 32 …) |
| Stage2 가중치 · `STAGE2_ARCH` | 32 → 34, 35 → 53, 51 → 54/55 |
| 노출 정의 · `EXPO_MODE` | 35 → 53, 51 → 54/55 (**위험도는 안 바뀌므로 PNG 재생성 불필요**) |
| 우선순위 규칙 · WUI 기준 | 34 · 35 · 51 |
| 행정동 경계 | 64 → 웹 (다른 단계와 무관) |

모델 교체는 `STAGE2_ARCH` 환경변수로 한다. 32번의 파일명 상수를 고치던 방식은
`_stage2_model.py` 로 대체됐다.

**재실행 비용** — 32 → 34는 1분 이내, 35(하루 13시각)는 1.5분, 25일 전체는 약 40분.
51번 전 기간은 세 시각 합쳐 **약 12시간**(_h10 240분 · _h08 197분 · 기본 11·14시 286분).
51번은 접미사 실행 시 결과도 접미사를 달고 나가므로, `daily_scan_all.csv` 를
갱신하려면 **마지막에 접미사 없이 한 번** 돌려야 한다.

---

## 7. 성능 특성

| 작업 | 소요 | 병목 |
|---|---|---|
| 지오코딩 복구 414건 | 6초 | SGIS API |
| 격자 대응표 생성 | 20초 | 로컬 계산 |
| 노출 레이어 | 1분 | CSV 파싱 |
| GRU 5-fold 학습 | 6.4분 | CPU (20코어) |
| 전국 격자 추론 1시각 | 0.6분 | NAS 래스터 읽기 |
| 사례 replay 13시각 | 1.5분 | 래스터 캐시로 75개만 읽음 |
| 발화직전 마커 구축 | 3.5시간 | **NAS SMB 왕복지연** |

NAS 접근이 압도적 병목이다. 초당 약 31건밖에 처리하지 못한다.
점 단위 추출이 많은 작업(21·31번)은 파일 접근을 전역으로 합치고 16스레드로 병렬화했다.
전국 격자를 다 읽는 작업(32·35번)은 반대로 전체 배열 읽기가 유리하며,
35번은 인접 시각이 시계열 12랙 중 11개를 공유한다는 점을 이용해 캐시한다.

---

## 8. 인증

```
C:\for_sgis\.env          # .gitignore 처리됨
  SGIS_CONSUMER_KEY=...
  SGIS_CONSUMER_SECRET=...
```

| 용도 | 엔드포인트 |
|---|---|
| 인증 (AccessToken, 4시간) | `https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json` |
| 지오코딩 | `.../OpenAPI3/addr/geocode.json` |
| 역지오코딩 | `.../OpenAPI3/addr/rgeocode.json` |
| 단계별 주소조회 | `.../OpenAPI3/addr/stage.json` |
| 격자경계 | `.../OpenAPI3/grid/data.geojson` |
| 인구·인구특성 | `.../OpenAPI3/stats/population.json` |
| 연령대별 인구 | `.../OpenAPI3/stats/searchpopulation.json` (`age_type` 22/23/24 = 유년/생산연령/고령) |
| 통계주제도 | `.../OpenAPI3/themamap/CTGR_00N/{list,data}.json` |
| 행정동 경계 | `.../OpenAPI3/boundary/hadmarea.geojson` |

**없는 것** — 격자통계 API(`stats/grid.json` 류), 통근·주간인구
(`stats/commute.json`, `stats/daytimepopulation.json`), 집계구 경계·통계.
500m 격자 연령별 인구는 **자료신청 다운로드가 유일한 경로**이며, 현재 받아 둔
`_census_reqdoc_*` 인구 파일에는 `to_in_001/007/008`(총인구·남·여) 3개뿐이다.

**함정** — `themamap` 은 `stat_thema_map_id` 가 틀려도 `errMsg: Success` 에 빈 배열을
돌려준다. 65번에 "한 건도 못 받으면 중단" 가드를 넣어 두었다.

`adm_cd`는 법정동코드가 아니라 **SGIS 자체 코드**다 (예: 강남구 = `11230`).
1일 5만 회 제한. 좌표 입출력은 EPSG:5179.
