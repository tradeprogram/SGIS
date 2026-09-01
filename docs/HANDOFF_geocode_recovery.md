# 인수인계: SGIS 지오코딩 복구 결과

## 0. 배경 — 왜 필요했나

`fire_events_geocoded.csv`의 2021~2025 산불시즌(2~6월) 사건 1,801건 중 **417건(23.2%)** 이
`geocode_status=fail`이라 lon/lat이 없고, `09_build_dataset_4v1.py`의 `geo['lon'].notna()`
필터에서 통째로 탈락했습니다.

탈락분 피해면적은 **19,633.1ha로 전체 34,540.0ha의 56.8%** 입니다.
여기에 2022-03-04 경북 울진 북면 두천 **16,301.98ha**(2022 울진·삼척 대형산불)가 포함됩니다.

즉 기존 모델은 기록상 최대 산불을 학습·검증에서 한 번도 보지 못했습니다.

## 1. 복구 결과 (최종)

  복구       414/417건 (99.3%)
  피해면적   19,632.9 / 19,633.1 ha (100.00%)
  정밀도     리(ri) 312건 / 동(dong) 57건 / 시군구(sgg) 45건

최종 실패 3건 (합계 0.14ha) — 무시 가능:
  경남 진해 진례 고모 산32-5      0.10ha
  경남 진해 진례 시례 산12        0.01ha
  충남 천안동안 성남 화성 102-6   0.03ha

※ 다른 세션에서 인용된 "379/417건, 98%"는 1차 패스 중간값입니다. 위 수치가 최종입니다.

## 2. 산출물 경로

  C:\for_sgis\data\grid_data\derived\fire_events_geocode_recovered.csv
  C:\for_sgis\data\grid_data\derived\sgis_admin_hierarchy.json

CSV 스키마:
  fire_id, datetime, damagearea, firecause, address_raw,
  matched_address, recover_level, x_5179, y_5179

`sgis_admin_hierarchy.json`은 시도→시군구→읍면동 정식명 캐시(시군구 252개)입니다.
재실행 시 이 파일이 있으면 API 호출 ~270회를 건너뜁니다.

## 3. 좌표계 주의 (가장 중요)

`x_5179 / y_5179`는 **EPSG:5179 (UTM-K)** 입니다. SGIS 지오코딩 API가 이 좌표계로 직접 반환합니다.
반면 `fire_events_geocoded.csv`의 `lon/lat`은 **EPSG:4326** 입니다. 되쓰려면 재투영해야 합니다.

    import pyproj
    tr = pyproj.Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(rec["x_5179"].values, rec["y_5179"].values)

참고로 09번은 어차피 lon/lat을 다시 EPSG:5179로 되돌려 행/열을 구합니다.
왕복 변환이 싫으면 `x_5179/y_5179`를 그대로 `rasterio.transform.rowcol`에 넣는 편이 정확합니다.

## 4. 원본 CSV가 두 곳에 있음 — 갱신 시 함정

  C:\sb\fire_label_build\fire_events_geocoded.csv        ← 09번이 실제로 읽는 경로
  V:\data\wildfire_reference\fire_events_geocoded.csv    ← NAS 사본

두 파일은 동일합니다 (5,435,813 bytes, MD5 ACCFFD74496E975B923C1E21196EB002).
**`09_build_dataset_4v1.py`의 `GEOCODED_CSV` 상수는 `C:\sb\...`를 가리킵니다.**
NAS만 갱신하면 재구축에 반영되지 않습니다. 둘 다 갱신하거나 09번 상수를 바꾸세요.
원본은 반드시 백업 후 수정.

## 5. 정밀도 필터 권고

`recover_level == 'sgg'` 45건은 **제외를 권합니다.**
시군구 중심점은 면적이 수백 km²라 500m 격자에 찍으면 발화 위치가 사실상 무작위가 됩니다.
기존 CSV가 이미 수용한 최저 정밀도는 `eup_myeon`(211건, 읍면 중심)이고,
`ri`·`dong`은 그와 동등하거나 더 정밀하지만 `sgg`는 훨씬 거칩니다.

  ri + dong                                    = 369건 (권장)
  위 중 09번 지속시간 필터(0 < end-start ≤ 72h) 통과 = 365건

기존 성공 건들의 정밀도 분포(참고):
  parcel_alt2 918 / parcel 218 / eup_myeon 211 / dong 24 / parcel_alt1 13

## 6. 스크립트

  C:\for_sgis\scripts\30_recover_failed_geocodes.py
      1차. 행정구역 계층 구축 + 축약주소 정규화 + 지오코딩          → 379건
  C:\for_sgis\scripts\30b_recover_remaining.py
      2차. 시도 편입 대응 (경북 군위 → 대구광역시 군위군)           → +6건
  C:\for_sgis\scripts\30c_recover_sigu.py
      3차. 시군구가 '시 + 구' 형태인 경우                          → +29건

30 → 30b → 30c 순서로 같은 CSV를 덮어쓰며 누적됩니다. 재실행 시 순서를 지키세요.

## 7. 인증

  C:\for_sgis\.env
      SGIS_CONSUMER_KEY=...
      SGIS_CONSUMER_SECRET=...

  인증          https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json
  지오코딩      https://sgisapi.mods.go.kr/OpenAPI3/addr/geocode.json
  단계별 주소   https://sgisapi.mods.go.kr/OpenAPI3/addr/stage.json

AccessToken 유효기간 4시간. 1일 5만 회 한도(API 이용약관 제4조 ②).
전체 복구에 쓴 호출은 약 2,500회로 한도에 여유가 큽니다. 실행 시간은 6초.

## 8. 핵심 기법 — 재구현하려면 이게 필요

SGIS 지오코딩은 축약 주소를 받지 못합니다.

  경북 의성 금성 청로            → errCd -100 (검색결과 없음)
  경상북도 의성군 금성면 청로리   → 성공

따라서 `addr/stage.json`으로 시도→시군구→읍면동 정식명 계층을 만들고 접두 매칭해 복원합니다.

시도 약칭 매핑이 필요합니다 (강원→강원특별자치도, 전북→전북특별자치도 등 최신 명칭 주의).

### 주소 컬럼 파싱 함정
`locgungu`가 `"청주 상당"`처럼 **두 토큰을 함께** 담는 경우가 있습니다.
이때 `locmenu`는 구 이름이 아니라 읍면 이름입니다.

  address_raw = '충북 청주 상당 미원 월용 산30'
  → locsi='충북', locgungu='청주 상당', locmenu='미원', locdong='월용'
  → 정답: '충청북도 청주시 상당구 미원면 월용리'

`locgungu`에 공백이 있으면 `{a}시 {b}구`로 조합하세요.
공백 없이 붙은 표기(`청주상당`, `포항북`, `용인처인`)도 실제로 존재합니다.

### 관할 시도 변경
`경북 군위`는 2023년 대구광역시로 편입됐습니다. 명시된 시도에서 못 찾으면
전국 시군구 역색인으로 재탐색해야 합니다.

## 9. damagearea 해석 주의 — 사례 선정 시 필수

이 CSV의 `damagearea`는 **발화 신고 시점 기록이며 최종 피해면적이 아닙니다.**

  2025 의성 산불 = '2025-03-22 13:57 경북 의성 금성 청로, 57.00ha' 단일 레코드
  2025-03-21~28 경북·경남·울산 전체 16건 합계도 4,567ha
  (실제 2025 영남 산불은 약 4.8만ha, 청송·영덕·영양 기록은 아예 없음)

따라서:
  - "의성 대형산불을 복구했다"는 부정확 → "의성 산불의 발화점 레코드를 복구"가 정확
  - `damagearea` 상위 정렬로 대형산불을 고르면 실제 최대 사건을 놓침
  - 단, **울진 2022(16,301.98ha)는 단일 대형 레코드로 실재**하며 복구 대상에 포함됨

## 10. 재구축 시 함께 점검하면 좋은 것

09번에는 지오코딩 외에도 다음 필터가 걸려 있습니다. 재구축 전에 각각의 탈락량을 찍어보세요.

  geo['lon'].notna()                                  ← 이번에 복구한 부분
  geo['end_dt'] > geo['start_dt']                     ← end_dt는 fire_raw_2015_2025.csv에서 옴
  (end_dt - start_dt).total_seconds() <= 72*3600      ← 72시간 컷오프
  DATA_CAP_2025 = 2025-06-26 06:00                    ← 2025년 기상 재수집 시점 제한
  month in [2,3,4,5,6]

`geo['end_dt'] = raw['end_dt'].values`는 **인덱스 정렬 없이 위치 기준으로 붙입니다.**
두 CSV의 행 순서가 어긋나면 조용히 잘못된 종료시각이 붙으므로 fire_id 기준 병합인지 확인이 필요합니다.
