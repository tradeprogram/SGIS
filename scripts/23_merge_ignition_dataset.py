"""
신규발화 학습셋 조립 — 마커(양성) + 하드네거티브 + 배경 음성.

구성
  양성    21번(기존 1,351건) + 31번(보충 383건) 의 pre_ignition 행
          40번이 확정한 사건 목록(720h 상한 + 거리필터 + SGIS 복구)에 있는 fire_id만 사용
  음성    같은 두 파일의 hard_neg + 기존 시퀀스 데이터셋의 배경 음성

왜 배경 음성을 재사용하는가
  Stage2 시퀀스(VPD·풍속 12h)를 1.36M행에 대해 새로 뽑으려면 시간별 래스터를
  수만 개 읽어야 해서 8시간이 걸린다. 그런데 시퀀스는 물리 관측값이라 라벨 방법론
  변경이나 모델 재학습과 무관하다. 배경 음성은 "화재가 아닌 임의의 시공간"이라는
  성격도 그대로다. 따라서 시퀀스는 재사용한다.

  다만 P_lgbm 은 다르다. LightGBM fold 모델이 2026-09-01 재학습되면서
  음성 P_lgbm 평균이 0.00204 → 0.00152 로 바뀌었다. 마커만 새 모델로 계산하고
  배경은 옛 값을 두면 두 집단 사이에 체계적 차이가 생겨 GRU가 그걸 학습한다.
  그래서 배경 음성의 P_lgbm 도 현재 모델로 전부 다시 계산한다.

  이를 위해 배경 행에 LightGBM 23피처 중 빠진 15개(지형 5 · 토지피복 6 · 인문 4)를
  래스터에서 다시 붙인다. 전부 정적이거나 연도별이라 전체 읽기 55회면 된다.

P_lgbm 은 연도별 OOF 규칙을 지킨다 — 그 해를 학습에 쓰지 않은 fold 모델이 그 해를 예측.
"""

import os, time
import numpy as np
import pandas as pd
import rasterio
import joblib

NAS      = r'V:\data'
MDL_DIR  = NAS + r'\ml_results\exp_no_smap_spi_temp_4v1\lgbm_models'
OLD_SEQ  = NAS + r'\ml_dataset\seq_dataset_12h_multih_4v1.parquet'
MASK     = NAS + r'\mask\common_mask_500m_5179.tif'
DERIVED  = r'C:\for_sgis\data\grid_data\derived'
OUT_PATH = os.path.join(DERIVED, 'seq_dataset_ignition_multih.parquet')

FEATURE_COLS = [
    'dem', 'slope', 'asp_cos', 'asp_sin', 'twi',
    'lc_urban', 'lc_deciduous', 'lc_conifer', 'lc_mixed_forest', 'lc_grass', 'lc_water',
    'pop_density', 'cropland', 'settlement_dist', 'road_density',
    'hum4d', 'prcp4d', 'vpd', 'wind', 'ndvi', 'ndmi', 'doy_sin', 'doy_cos',
]
# 옛 시퀀스 데이터셋에 없어서 래스터에서 다시 붙여야 하는 것들
RASTER_COLS = FEATURE_COLS[:15]

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

t0 = time.time()

# ── 1. 마커 — 40번 사건 목록에 있는 것만 ─────────────────────────────
summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
valid_ids = set(summ['fire_id'].unique())

parts = []
for f in ['preignition_markers_raw.parquet', 'preignition_markers_topup.parquet']:
    p = os.path.join(DERIVED, f)
    if not os.path.exists(p):
        print(f'  [없음] {f}')
        continue
    d = pd.read_parquet(p)
    before = len(d)
    d = d[d['fire_id'].isin(valid_ids)]
    parts.append(d)
    print(f'  {f}: {before:,} → {len(d):,}행 '
          f'(사건 {d.loc[d["sample_type"]=="pre_ignition","fire_id"].nunique():,}건)')
mk = pd.concat(parts, ignore_index=True)
mk = mk.drop_duplicates(['prow', 'pcol', 'year', 'month', 'day', 'hour'])
n_ev = mk.loc[mk['sample_type'] == 'pre_ignition', 'fire_id'].nunique()
print(f'마커 합계: {len(mk):,}행  발화 사건 {n_ev:,}건  {mk["sample_type"].value_counts().to_dict()}')

# ── 2. 배경 음성 — 시퀀스는 재사용, 화재 진행중 행은 제외 ────────────
old = pd.read_parquet(OLD_SEQ)
bg = old[old['label'] == 0].copy()
leftover = int(bg[LABELS].values.sum())
if leftover:
    bg = bg[bg[LABELS].sum(axis=1) == 0].copy()
bg['sample_type'] = 'neg_bg'
print(f'\n배경 음성: {len(bg):,}행 (기존 시퀀스 재사용, 잔여 양성 {leftover}개 제거)')

# ── 3. 배경 행에 빠진 15피처를 래스터에서 붙인다 ─────────────────────
print('\n배경 행에 지형·토지피복·인문환경 피처 부착 중...')
rows_arr = bg['prow'].astype(int).values
cols_arr = bg['pcol'].astype(int).values

for name, path in {
    'dem':     NAS + r'\DEM\500m_aligned\dem_500m_5179.tif',
    'slope':   NAS + r'\DEM\500m_aligned\slope_500m_5179.tif',
    'asp_cos': NAS + r'\DEM\500m_aligned\aspect_500m_cos_5179.tif',
    'asp_sin': NAS + r'\DEM\500m_aligned\aspect_500m_sin_5179.tif',
    'twi':     NAS + r'\DEM\500m_aligned\twi_500m_5179.tif',
}.items():
    with rasterio.open(path) as s:
        arr = s.read(1).astype(np.float32); nd = s.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    bg[name] = arr[rows_arr, cols_arr]
    del arr
print('  지형 완료')

for year in sorted(bg['year'].unique()):
    idx = (bg['year'] == year).values
    r, c = rows_arr[idx], cols_arr[idx]
    for lc in ['urban', 'deciduous', 'conifer', 'mixed_forest', 'grass', 'water']:
        p = NAS + rf'\landcover_raster\landcover_{lc}_ratio_{year}.tif'
        if os.path.exists(p):
            with rasterio.open(p) as s:
                arr = s.read(1).astype(np.float32); nd = s.nodata
            if nd is not None: arr[arr == nd] = np.nan
            bg.loc[idx, f'lc_{lc}'] = arr[r, c]; del arr
        else:
            bg.loc[idx, f'lc_{lc}'] = np.nan
    dens_year = year if year <= 2024 else 2024
    road_year = 2021 if year == 2021 else (2025 if year == 2025 else 2022)
    for p, col in [
        (NAS + rf'\people_density\output\04density_aligned\people_density_{dens_year}_04_epsg5179_500m.tif', 'pop_density'),
        (NAS + rf'\cropland\cropland_ratio_{year}_500m.tif', 'cropland'),
        (NAS + rf'\settlement\distance_to_settlement_{year}_500m.tif', 'settlement_dist'),
        (NAS + rf'\road_distance\road_length_density_aligned\road_length_density_{road_year}_500m.tif', 'road_density'),
    ]:
        if os.path.exists(p):
            with rasterio.open(p) as s:
                arr = s.read(1).astype(np.float32); nd = s.nodata
            if nd is not None: arr[arr == nd] = np.nan
            bg.loc[idx, col] = arr[r, c]; del arr
    print(f'  연도 {year} 완료  ({(time.time()-t0)/60:.1f}분)')

# 옛 시퀀스 데이터셋에는 vpd/wind 단일 컬럼이 없다 (t0 가 곧 T-1h 값)
bg['vpd'], bg['wind'] = bg['vpd_t0'], bg['wind_t0']

# ── 4. P_lgbm 전량 재계산 (현재 fold 모델, 연도별 OOF) ───────────────
print('\nP_lgbm 재계산 (2026-09-01 재학습 모델, 연도별 OOF)')
both = pd.concat([mk, bg], ignore_index=True, sort=False)
both = both.drop_duplicates(['prow', 'pcol', 'year', 'month', 'day', 'hour'], keep='first')

old_mean = float(bg['P_lgbm'].mean())
both['P_lgbm'] = np.nan
for i, ty in enumerate(YEARS):
    m = (both['year'] == ty).values
    if m.sum() == 0:
        continue
    mp = os.path.join(MDL_DIR, f'lgbm_fold{i+1}_test{ty}.pkl')
    if not os.path.exists(mp):
        raise SystemExit(f'LightGBM 모델 없음: {mp}')
    model = joblib.load(mp)
    X = both.loc[m, FEATURE_COLS].values.astype(np.float32)
    ok = ~np.isnan(X).any(axis=1)
    vals = np.full(m.sum(), np.nan, dtype=np.float32)
    if ok.sum():
        vals[ok] = model.predict_proba(X[ok])[:, 1]
    both.loc[m, 'P_lgbm'] = vals
    print(f'  {ty}: {int(m.sum()):,}행  결측입력 {int((~ok).sum()):,}  '
          f'P_lgbm 평균 {np.nanmean(vals):.5f}')

n_na = int(both['P_lgbm'].isna().sum())
both['P_lgbm'] = both['P_lgbm'].fillna(0.0)
print(f'P_lgbm 결측 {n_na:,}개 → 0으로 채움')
print(f'배경 음성 P_lgbm 평균: {old_mean:.5f}(옛) → '
      f'{float(both.loc[both["sample_type"]=="neg_bg","P_lgbm"].mean()):.5f}(새)')

# ── 5. 저장 ──────────────────────────────────────────────────────────
keep = [c for c in META_COLS + STATIC_COLS + SEQ_COLS + LABELS if c in both.columns]
out = both[keep].reset_index(drop=True)
out.to_parquet(OUT_PATH, index=False)

print(f'\n{"="*66}')
print(f'저장: {OUT_PATH}')
print(f'shape: {out.shape}  ({(time.time()-t0)/60:.1f}분)')
print(f'sample_type: {out["sample_type"].value_counts().to_dict()}')
print('\nhorizon별 양성:')
for H in HORIZONS:
    n = int(out[f'label_t{H}'].sum())
    print(f'  label_t{H}=1: {n:,}  (1:{len(out)//max(n,1):,})')
print('\n연도별 양성:')
print(out.groupby('year')[LABELS].sum().astype(int).to_string())
na_rows = int(out[STATIC_COLS + SEQ_COLS].isna().any(axis=1).sum())
print(f'\n학습 전 dropna 대상: {na_rows:,}행 ({100*na_rows/len(out):.2f}%)')
print(out.assign(_na=out[STATIC_COLS + SEQ_COLS].isna().any(axis=1))
         .groupby('sample_type')['_na'].agg(['sum', 'count']).to_string())
