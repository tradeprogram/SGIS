"""
신규발화 학습셋 조립: 마커/하드네거티브 + 기존 배경 음성 → seq_dataset_ignition_multih.parquet

구성
  양성  : 21번이 만든 pre_ignition 행 (화재 1,360건 × T-1/-2/-3h, horizon별 label_t{H}=1)
  음성  : 21번이 만든 hard_neg 행 (발화픽셀 반경 1~10km, 같은 시각)
          + 기존 seq_dataset_12h_multih_4v1의 배경 음성 (전국 무작위)

기존 데이터셋에서 label==1(화재 진행 중) 행은 전부 제외한다.
  - 20_label_audit 결과 기존 label_t1/t2/t3 양성은 100%가 label==1 행이었으므로,
    label==1을 빼면 지속(persistence) 양성이 전부 사라진다.
  - model2 설계와 동일: "이미 불난 픽셀에 3시간 안에 새로 불붙는가"는 과제 정의상 무의미.

P_lgbm은 11_lgbm_inference_4v1.py와 동일한 OOF 규칙으로 부여한다
  (해당 연도를 학습에 쓰지 않은 fold 모델이 그 연도를 예측 → Stage1→Stage2 누수 차단).

주의: exp_no_smap_spi_temp_4v1/lgbm_models의 저장 파일은 model2 README 기록에 따르면
      ratio 스윕 마지막 값(50)으로 덮어써진 것이다(11번 주석은 ratio=10이라고 적혀 있음).
      다만 기존 데이터셋의 P_lgbm도 같은 파일로 만들어졌으므로, 배경 음성과 마커의
      P_lgbm 정의를 일치시키려면 동일 파일을 그대로 써야 한다.
"""

import os
import numpy as np
import pandas as pd
import joblib

NAS        = r'V:\data'
MDL_DIR    = NAS + r'\ml_results\exp_no_smap_spi_temp_4v1\lgbm_models'
OLD_SEQ    = NAS + r'\ml_dataset\seq_dataset_12h_multih_4v1.parquet'
MARKER     = r'C:\for_sgis\data\grid_data\derived\preignition_markers_raw.parquet'
OUT_PATH   = r'C:\for_sgis\data\grid_data\derived\seq_dataset_ignition_multih.parquet'

FEATURE_COLS = [
    'dem', 'slope', 'asp_cos', 'asp_sin', 'twi',
    'lc_urban', 'lc_deciduous', 'lc_conifer', 'lc_mixed_forest', 'lc_grass', 'lc_water',
    'pop_density', 'cropland', 'settlement_dist', 'road_density',
    'hum4d', 'prcp4d',
    'vpd', 'wind',
    'ndvi', 'ndmi',
    'doy_sin', 'doy_cos',
]
YEARS    = [2021, 2022, 2023, 2024, 2025]
HORIZONS = [1, 2, 3]
LOOKBACK = 12

SEQ_COLS = []
for lag in range(LOOKBACK - 1, 0, -1):
    SEQ_COLS += [f'vpd_tm{lag}', f'wind_tm{lag}']
SEQ_COLS += ['vpd_t0', 'wind_t0']
STATIC_COLS = ['P_lgbm', 'ndvi', 'ndmi', 'hum4d', 'prcp4d', 'doy_sin', 'doy_cos']
LABELS      = [f'label_t{H}' for H in HORIZONS]
META_COLS   = ['prow', 'pcol', 'year', 'month', 'day', 'hour', 'sample_type']

# ── 1. 마커 + 하드네거티브 로드, P_lgbm OOF 부여 ──────────────────────
mk = pd.read_parquet(MARKER)
print(f'마커 파일: {mk.shape}  {mk["sample_type"].value_counts().to_dict()}')

mk['P_lgbm'] = np.nan
for i, test_year in enumerate(YEARS):
    mask = mk['year'] == test_year
    if mask.sum() == 0:
        continue
    mdl_path = os.path.join(MDL_DIR, f'lgbm_fold{i+1}_test{test_year}.pkl')
    if not os.path.exists(mdl_path):
        print(f'  [경고] 모델 없음: {mdl_path}')
        continue
    model = joblib.load(mdl_path)
    X = mk.loc[mask, FEATURE_COLS].values.astype(np.float32)
    mk.loc[mask, 'P_lgbm'] = model.predict_proba(X)[:, 1]
    print(f'  test={test_year}: {int(mask.sum()):,}행  P_lgbm 평균={mk.loc[mask,"P_lgbm"].mean():.4f}')

print(f'P_lgbm 결측: {int(mk["P_lgbm"].isna().sum()):,}개 → 0으로 채움 (11번과 동일)')
mk['P_lgbm'] = mk['P_lgbm'].fillna(0.0)

mk_out = mk[[c for c in META_COLS + STATIC_COLS + SEQ_COLS + LABELS if c in mk.columns]].copy()

# ── 2. 기존 배경 음성 로드 (화재 진행중 행 제외) ─────────────────────
old = pd.read_parquet(OLD_SEQ)
print(f'\n기존 시퀀스 데이터셋: {old.shape}')
print(f'  label==1(화재 진행중): {int(old["label"].sum()):,}행 → 제외')

bg = old[old['label'] == 0].copy()
leftover = int(bg[LABELS].values.sum())
print(f'  배경 음성으로 사용: {len(bg):,}행 (잔여 label_t 양성 {leftover}개 — 0이어야 정상)')
if leftover > 0:
    bg = bg[bg[LABELS].sum(axis=1) == 0].copy()
    print(f'  잔여 양성 제거 후: {len(bg):,}행')

bg['sample_type'] = 'neg_bg'
bg_out = bg[[c for c in META_COLS + STATIC_COLS + SEQ_COLS + LABELS if c in bg.columns]].copy()

# ── 3. 병합 ──────────────────────────────────────────────────────────
df = pd.concat([mk_out, bg_out], ignore_index=True)

# 마커/하드네거티브가 배경과 같은 (픽셀, 시각)에 겹칠 경우 마커를 우선 유지
before = len(df)
df = df.drop_duplicates(subset=['prow', 'pcol', 'year', 'month', 'day', 'hour'], keep='first')
print(f'\n중복 제거: {before:,} → {len(df):,}행')

df = df.reset_index(drop=True)
df.to_parquet(OUT_PATH, index=False)

print(f'\n저장: {OUT_PATH}')
print(f'shape: {df.shape}')
print(f'sample_type: {df["sample_type"].value_counts().to_dict()}')
print('\nhorizon별 양성:')
for H in HORIZONS:
    n = int(df[f'label_t{H}'].sum())
    print(f'  label_t{H}=1: {n:,}  (양성비율 1:{len(df)//max(n,1):,})')

print('\n연도별 양성:')
print(df.groupby('year')[LABELS].sum().astype(int).to_string())

na_rows = int(df[STATIC_COLS + SEQ_COLS].isna().any(axis=1).sum())
print(f'\n학습 전 dropna 대상(피처 결측): {na_rows:,}행 ({100*na_rows/len(df):.2f}%)')
print('sample_type별 결측:')
print(df.assign(_na=df[STATIC_COLS + SEQ_COLS].isna().any(axis=1))
        .groupby('sample_type')['_na'].agg(['sum', 'count']).to_string())
