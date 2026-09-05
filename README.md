<div align="center">

# 산불 발화예측·우선대응 통합지도

**SGIS 통계지리 기반 500m 격자 의사결정 시스템**

전국 500m 격자의 향후 1~3시간 **산불 발화**를 AI로 예측하고,
여기에 **SGIS 인구·가구·주택 격자통계**를 결합해
"어디가 위험한가"를 넘어 **"어디를 먼저 지킬 것인가"** 에 답한다.

[데모](https://wildfire-predict-framework.vercel.app) ·
[아키텍처](#아키텍처) · [성능](#성능) · [실행](#실행) · [저장소 구조](#저장소-구조)

![격자](https://img.shields.io/badge/분석격자-403%2C385셀_·_500m-2c7fb8)
![산불](https://img.shields.io/badge/산불사건-1%2C734건_·_2021~2025-2c7fb8)
![SGIS](https://img.shields.io/badge/SGIS_노출인구-51.6M_배분-41ab5d)
![전기간](https://img.shields.io/badge/전_기간_산출-737일-41ab5d)

</div>

---

## 무엇을 하는가

| | |
|---|---|
| **입력** | 기상(VPD·풍속·습도·강수) · 위성 식생(NDVI·NDMI) · 지형 · 토지피복 · 인문환경 · SGIS 격자통계 |
| **모델** | Stage1 LightGBM(공간 취약도) → Stage2 GRU(12시간 시계열) |
| **출력** | 격자별 **t+1h · t+2h · t+3h** 신규 발화 위험 순위 |
| **결합** | 위험도 백분위 × SGIS **주간 보정** 노출 백분위 → 대응 우선지역 Top-N |
| **서비스** | 전 기간 737일 타임라인 · 사례 25일 시간대별 · 행정동 검색 · 분석 에이전트 |

출력은 **확률이 아니라 전국 상대 백분위**다. 학습에 재표본화를 써서 sigmoid 값을
발생확률로 읽을 수 없다. 화면·문서 모두 "전국 상위 N%"로만 표현한다.

---

## 규모

| 항목 | 실측값 |
|---|---|
| 분석 격자 | EPSG:5179 · 500m · 2130 × 2123 · 유효 **403,385셀** |
| 산불 사건 | 2021~2025년 2~6월 · **1,734건** |
| 화재 라벨 | 화재 셀 **2,090개** → 셀×시각 **17,085행** (24h 상한) |
| SGIS 격자통계 | 인구·가구·주택 500m 격자 · 2024 기준 |
| 노출 배분 | 총인구 **51,637,925명** (원본의 99.47%) |
| 지오코딩 복구 | **414/417건 (99.3%)** · 누락 피해면적의 100% |
| 전 기간 산출 | **737일** (2021~2025, 매년 2~6월) × 최대 4개 시각 |
| 시간대별 상세 | 사례 25일 (연도별 피해상위 3 + 무작위 2, seed 42) |
| SGIS 행정동 | 경계·인구특성 **3,559개** |

---

## 아키텍처

```mermaid
flowchart TB
    subgraph S1["① 데이터 — 공통 500m 격자로 정렬"]
        A1[산불 이력<br/>KFS 발생대장 · dNBR 폴리곤]
        A2[기상·위성<br/>VPD · 풍속 · NDVI · NDMI · 습도 · 강수]
        A3[지형·인문<br/>DEM · 토지피복 · 도로 · 정주지]
        A4[SGIS 격자통계<br/>인구 · 가구 · 주택]
    end
    subgraph S2["② 라벨 — 3중 필터"]
        B1[거리필터<br/>타원확산 기하 · 계수 3]
        B2[24시간 상한]
        B3[720시간 지속상한]
    end
    subgraph S3["③ 모델 — 2단계"]
        C1[Stage 1 · LightGBM<br/>23피처 → P_lgbm<br/>연도별 OOF]
        C2[Stage 2 · GRU<br/>VPD·풍속 12h + 정적 7<br/>t+1/2/3h 동시출력]
    end
    subgraph S4["④ 결합 — SGIS"]
        D1[격자 정합<br/>4셀 면적가중 배분]
        D2[노출 레이어<br/>주간 보정 인구]
    end
    subgraph S5["⑤ 의사결정"]
        E1[위험 백분위]
        E2[주간 노출 백분위]
        E3[WUI 한정 → 대응 우선지역 Top-N]
    end
    subgraph S6["⑥ 운영 산출 — 737일"]
        F1[일별 전국 지도<br/>10시 산출 → t+1h 위험도]
        F2[시간축 등급<br/>5년 중 오늘의 위치]
        F3[웹 타임라인 · 분석 에이전트]
    end
    S1 --> S2 --> S3 --> S4 --> S5 --> S6
```

### 모델

```
Stage 1 — LightGBM (기존 연구 산출물)
  입력  23피처 (지형 5 · 토지피복 6 · 인문 4 · 기상 4 · 식생 2 · 계절 2)
  출력  P_lgbm — 해당 시점 공간 취약도
  누수  연도별 OOF — 그 해를 학습에 쓰지 않은 fold 모델이 그 해를 예측

Stage 2 — GRU (본 저장소, 배포 기본값 STAGE2_ARCH=gru_old)
  시계열  VPD · 풍속 × 12시간              shape (batch, 12, 2)
  정적    P_lgbm · NDVI · NDMI · hum4d · prcp4d · doy_sin · doy_cos   (7)
  인코더  nn.GRU(2 → 64, num_layers=2, dropout=0.3)
  헤드    Linear(64+7 → 64) → ReLU → Dropout → Linear(64 → 3) → Sigmoid
  출력    t+1h · t+2h · t+3h 동시
  검증    연도별 leave-one-year-out 5-fold
```

시각 규약 — `vpd_t0` = 대상시각 −1h 래스터, `vpd_tm{k}` = 대상시각 −(k+1)h,
예측 대상은 대상시각 +1h/+2h/+3h.

v4b CNN 대안은 검증했으나 채택하지 않았다. 근거는 [STAGE2_ABLATION.md](docs/STAGE2_ABLATION.md).

### 노출 — 주간 보정

우선순위의 노출항은 상주인구가 아니라 주간 보정 인구다.

```
day_idx   = (종사자 + 65세이상 + 15세미만) / 상주인구     전국 중앙값 0.77
pop_day   = pop_total × day_idx
expo_rank = pop_day 의 백분위
```

산불은 오후에 집중되는데 상주인구는 야간 기준이라 그대로 쓰면 시각이 어긋난다.
SGIS에 주간인구·통근 통계가 없어 종사자 통계로 근사한 **추정치**이며,
농작업이 사업체 등록에 잡히지 않아 농촌을 과소추정한다.
`EXPO_MODE=resident` 로 보정 전 동작으로 돌아갈 수 있다.

고령·노후주택은 산식에 넣지 않는다. 거주 격자끼리 비교하면 발화지의 고령비율
0.91배, 노후주택비율 0.89배로 상관이 없어 표시용으로만 쓴다.

---

## 성능

### 신규발화 GRU — 연도별 leave-one-year-out 5-fold

| horizon | AUROC | AUPRC | 평균 기저율 | 기저율 대비 리프트 |
|---|---|---|---|---|
| t+1h | **0.837** | 0.142 | 2.97% | **5.15배** |
| t+2h | 0.826 | 0.127 | 2.96% | 4.57배 |
| t+3h | 0.807 | 0.115 | 2.96% | 4.06배 |

리프트는 fold별 기저율(1.94~4.24%)로 각각 나눈 뒤 평균한 값이다.
기저율이 fold마다 달라 AUPRC 절대값을 fold 사이에서 직접 비교해서는 안 된다.

### 전국 격자를 상대로 한 실전 순위

실제 발화 **1,308건** 전수. 각 사건이 가장 높게 잡힌 시각 기준으로 집계한다.

| 구간 | 포착률 | 무작위 대비 |
|---|---|---|
| 전국 상위 1% | **6.0%** | 6.0배 |
| 전국 상위 5% | 16.8% | 3.4배 |
| 전국 상위 10% | 26.0% | 2.6배 |
| 전국 상위 20% | 40.0% | 2.0배 |

피해규모가 클수록 순위가 높다 — 100ha 이상 중앙값 **10.5%**, 0.5ha 미만 30.2%.
예보 시계가 멀어질수록 날카로운 쪽이 먼저 무너진다(상위 1%: t+1h 6.0% → t+3h 3.3%).

> **읽는 법.** "여기서 불이 난다"를 맞히는 도구가 아니라 **한정된 진화 자원을 어디에
> 먼저 배치할지 좁혀주는 도구**다. 상위 1%는 전국 4,034셀, 상위 5%는 20,169셀이다.

### 시간축 위험등급

지도의 등급은 그날 하루 안에서의 공간 백분위라, 발화 0건인 조용한 날에도 상위 1%는
항상 나온다. 그래서 737일 분포에서 오늘의 위치를 따로 잰다.

| 시간축 구간 | 일수 | 평균 발화 | 평균 피해 | 발화 0건인 날 |
|---|---|---|---|---|
| 하위 25% | 186 | 0.15건 | 0.01 ha | **161일** |
| 25–50% | 185 | 0.85건 | 0.24 ha | 100일 |
| 50–75% | 185 | 2.54건 | 29.9 ha | 37일 |
| **상위 25%** | 185 | **5.82건** | **156.4 ha** | **4일** |
| (등급 `매우 높음`) | 114 | 6.54건 | — | **0일** |

발화건수와의 스피어만 상관 **0.769**.

상세 검증 과정과 ablation 은 [METHODOLOGY.md](docs/METHODOLOGY.md) ·
[STAGE2_ABLATION.md](docs/STAGE2_ABLATION.md).

---

## 실행

```bash
# ① SGIS 지오코딩 복구 — 다른 단계의 입력
python scripts/30_recover_failed_geocodes.py
python scripts/30b_recover_remaining.py
python scripts/30c_recover_sigu.py

# ② 격자 정합 (SGIS 자료 수령 후)
python scripts/22_sgis_grid_weights.py
python scripts/26_build_mask_to_sgis_lookup.py
python scripts/28_build_gridcd_lookup.py
python scripts/29_build_exposure_layer.py

# ③ 화재 라벨 · 행정동 · SGIS 인구특성
python scripts/40_build_fire_cell_index.py
python scripts/41_build_cell_admin_lookup.py
python scripts/64_build_adm_boundaries.py
python scripts/65_fetch_sgis_vulnerability.py

# ④ 모델
python scripts/24_train_gru_ignition_5fold.py

# ⑤ 추론 · 사례일
$env:TARGET_DT='2025-03-22 12:00'; python scripts/32_fullgrid_inference_ignition.py
$env:DATE='2025-03-22';            python scripts/35_case_replay.py

# ⑥ 전 기간 스캔 — 시각당 약 4~5시간. 마지막은 접미사 없이 돌려야 통합 CSV 가 갱신된다
$env:FORCE='1'; $env:REGION_AGG='1'; $env:SCAN_HOURS='10'; $env:OUT_SUFFIX='_h10'
python scripts/51_daily_scan_full_period.py

# ⑦ 웹 자산
python scripts/52_select_case_days.py
python scripts/53_build_multiday_assets.py
python scripts/54_time_axis_risk.py
python scripts/55_build_timeline_index.py
python scripts/56_build_region_daily.py
```

### 웹

```bash
cd web && npm install && npx next build && npx next start -p 3100
```

개발 서버(`npm run dev`)는 이 환경에서 하이드레이션에 실패하므로 프로덕션 빌드로 확인한다.
분석 에이전트에는 `GEMINI_API_KEY` 가 필요하다.

### 주요 환경변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `STAGE2_ARCH` | `gru_old` | `gru_old` / `cnn` / `gru` |
| `STAGE2_RATIO` · `STAGE1_RATIO` | `20` | 재표본화 비율 |
| `EXPO_MODE` | `day` | `day`(주간 보정) / `resident` |
| `SCAN_HOURS` · `OUT_SUFFIX` · `FORCE` | — | 51번 스캔 제어 |
| `REGION_AGG` | — | `1` 이면 행정동 집계도 저장 |

---

## 저장소 구조

```
wildfire_predict_framework/
├── scripts/                     분석 파이프라인
│   ├── 20~25_*.py                 라벨 감사 · 발화직전 마커 · 학습셋 · GRU 5-fold
│   ├── 26~29_*.py                 격자 대응표 · GRID_CD 매핑 · 노출 레이어
│   ├── 30~30c_*.py                SGIS 지오코딩 복구 (3단계)
│   ├── 32_fullgrid_inference_*.py 전국 격자 추론
│   ├── 34_priority_final.py       대응 우선순위 (WUI 한정)
│   ├── 35_case_replay.py          시간대별 사례 replay + occlusion 기여도
│   ├── 40~41_*.py                 화재 셀-시간 인덱스 · 격자→행정동
│   ├── 51_daily_scan_full_period.py  전 기간 737일 스캔 (+ 행정동 집계)
│   ├── 52~56_*.py                 사례일 선정 · 웹 자산 · 시간축 등급 · 타임라인 · 지역 집계
│   ├── 60~63_*.py                 v4b CNN 구조 검증 · 재현 · 재학습 · A/B 비교
│   ├── 64~65_*.py                 SGIS 행정동 경계 · 인구특성 수집
│   ├── 67~69_*.py                 보고서 그림 · 문서 · UI 캡처
│   ├── _stage2_model.py           Stage1+Stage2 로드 (32·35·51 공유)
│   └── _exposure.py               격자별 노출 · 주간 보정 (35·51 공유)
├── web/                         Next.js + MapLibre 프런트엔드
│   ├── src/pages/index.tsx        전 기간 모드 · 시간대별 상세 모드
│   ├── src/components/            RegionPicker · ChatPanel · WhyPanel
│   ├── src/lib/spatialQuery.ts    분석 에이전트 공간질의
│   └── public/data/               타임라인 · 일별 PNG · 사례일 자산
├── docs/
│   ├── ARCHITECTURE.md            데이터 흐름 · 스크립트 의존관계 · 재실행 비용
│   ├── METHODOLOGY.md             라벨 필터 · 격자 정합 · 우선순위 규칙
│   ├── STAGE2_ABLATION.md         모델 선택 근거
│   └── REPORT.md                  공모전 보고서 원고
├── outputs/                     성능 결과표 · 그림 · 실행 로그
└── data/                        (미포함 — 원 권리는 각 제공기관)
    └── grid_data/derived/         결과표·파라미터만 공개
```

`data/` 는 1.8GB이고 원 권리가 각 제공기관에 있어 포함하지 않는다.
SGIS 격자통계·격자경계는 [자료제공](https://sgis.mods.go.kr/view/pss/openDataIntrcn)에서 신청한다.
재현에 필요한 격자 정합 가중치·거리필터 로그·결과표는 `data/grid_data/derived/` 에 포함했다.

---

## 데이터 출처

| 데이터 | 출처 | 이용 |
|---|---|---|
| 500m 격자통계 (인구·가구·주택) | 국가데이터처 SGIS 자료제공 | 무료 · 신청 후 다운로드 |
| 500m 격자경계 · 행정동 경계 | 국가데이터처 SGIS | 무료 · SHP |
| 지오코딩 · 행정구역 · 인구특성 · 통계주제도 | SGIS OpenAPI | 무료 · 1일 5만 회 |
| 산불 발생대장 | 산림청 | 과제 내부 |
| dNBR 피해 폴리곤 | Sentinel-2 · Google Earth Engine | 과제 내부 |
| 기상 (VPD·풍속·습도·강수) | 기상청 | 과제 내부 |
| 식생 (NDVI·NDMI) | MODIS MOD09A1 | NASA |
| 지형·토지피복·도로·정주지 | 국토지리정보원 · 환경부 · 국토교통부 | 과제 내부 |

> SGIS 통계 비공개 처리 — 인구부문은 값이 5 미만이면 0/5/8로 치환된다.
> 침엽수 80% 이상 격자의 **71.7%** 가 이 구간이라, 산불 위험지역일수록 개별 값의
> 상대오차가 크다. 저인구 격자에는 품질 플래그를 함께 저장한다.

---

## 한계

1. **주간인구는 추정치다.** 농작업이 사업체 등록에 잡히지 않아 농촌을 과소추정한다.
2. **소형 화재 식별력이 낮다.** 0.5ha 미만은 순위 중앙값 30.2%다.
3. **평가에서 빠진 발화가 있다.** 하루 4개 시각만 스캔해 심야·저녁 발화 일부가 미평가다.
4. **확률 보정 미완.** 재표본화 때문에 출력을 발생확률로 쓸 수 없다.
5. **입력 데이터가 2025년 6월에서 끝난다.** 시간별 기상 래스터 보유 범위 때문이다.
6. **전 기간 모드는 격자 단위 값을 저장하지 않는다.** 공간질의가 시군구까지만 답한다.
7. **`damagearea` 는 최종 피해면적이 아니다.** 발화 신고시점 기록이다.

---

## 라이선스

코드는 [MIT](LICENSE). 수집·가공 데이터의 원 권리는 각 제공기관에 있으며 재이용 조건은 각 기관 고지를 따른다.
SGIS OpenAPI 인증키는 [API 이용약관](https://sgis.mods.go.kr/developer/html/newOpenApi/app/rules.html) 제4조에 따라
발급받은 자만 이용할 수 있고 타인과 공유할 수 없다.
