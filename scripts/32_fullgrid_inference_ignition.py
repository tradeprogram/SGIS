"""
신규발화 GRU 전국 500m 격자 추론 (t+1h/t+2h/t+3h).

model2/scripts/full_grid_inference_gru_multih.py를 신규발화 모델용으로 이식.
바뀐 점
  - 모델: gru_ign_fold5_test2025_*  (24번에서 학습한 신규발화 모델)
  - TARGET_DT를 환경변수로 파라미터화 (사례 replay를 여러 시각에 돌리기 위함)
  - 출력 경로를 derived/ 로 통일

시각 규약 (12_build_seq_dataset_4v1.py와 동일)
  vpd_t0    = TARGET_DT - 1h 래스터
  vpd_tm{k} = TARGET_DT - (k+1)h 래스터
  예측 대상 = TARGET_DT + 1h / +2h / +3h 의 신규 발화

주의: 이 스크립트가 쓰는 fold5 모델은 2021~2024 학습 / 2025 test 이므로,
      2025년 시각에 대한 추론은 out-of-sample이다. 다른 연도에 돌리려면
      해당 연도를 test로 둔 fold 모델과 LightGBM 모델로 바꿔야 누수가 없다.
"""

import os, glob, re, time, pickle
import numpy as np
import pandas as pd
import rasterio
import joblib
import torch
import torch.nn as nn

NAS       = r'V:\data'
MDL_DIR   = r'C:\for_sgis\models'
OUT_DIR   = r'C:\for_sgis\data\grid_data\derived'
HORIZONS  = [1, 2, 3]

TARGET_DT = pd.Timestamp(os.environ.get('TARGET_DT', '2025-03-22 12:00'))
YEAR      = TARGET_DT.year
FOLD_YEAR = 2025          # 사용할 fold 모델의 test 연도 (누수 방지용으로 YEAR와 같아야 함)

os.makedirs(OUT_DIR, exist_ok=True)
t0 = time.time()
print(f'대상 시각: {TARGET_DT}  → 예측 {[f"t+{h}h" for h in HORIZONS]}')
print(f'모델: 신규발화 GRU fold5 (2021~2024 학습 / {FOLD_YEAR} test)')
if YEAR != FOLD_YEAR:
    print(f'  [경고] TARGET 연도({YEAR}) != fold test 연도({FOLD_YEAR}) — 학습에 본 해라 누수 가능')

with rasterio.open(NAS + r'\mask\common_mask_500m_5179.tif') as src:
    mask_arr  = src.read(1)
    transform = src.transform
    shape     = (src.height, src.width)
valid_rows, valid_cols = np.where(mask_arr == 1)
n_valid = len(valid_rows)
print(f'유효 픽셀: {n_valid:,}개 | 격자: {shape}')

_cache = {}


def read_at(path, rows, cols):
    if path not in _cache:
        with rasterio.open(path) as s:
            arr = s.read(1).astype(np.float32)
            nd = s.nodata
        if nd is not None:
            arr[arr == nd] = np.nan
        _cache[path] = arr
    return _cache[path][rows, cols]


# ── Stage1 23피처 ────────────────────────────────────────────────────
print('\n[Stage1] LightGBM 23피처 추출 중...')
feat = {}
for name, path in {
    'dem':     NAS + r'\DEM\500m_aligned\dem_500m_5179.tif',
    'slope':   NAS + r'\DEM\500m_aligned\slope_500m_5179.tif',
    'asp_cos': NAS + r'\DEM\500m_aligned\aspect_500m_cos_5179.tif',
    'asp_sin': NAS + r'\DEM\500m_aligned\aspect_500m_sin_5179.tif',
    'twi':     NAS + r'\DEM\500m_aligned\twi_500m_5179.tif',
}.items():
    feat[name] = read_at(path, valid_rows, valid_cols)
    _cache.clear()
print('  지형 완료')

for lc in ['urban', 'deciduous', 'conifer', 'mixed_forest', 'grass', 'water']:
    p = NAS + rf'\landcover_raster\landcover_{lc}_ratio_{YEAR}.tif'
    feat[f'lc_{lc}'] = read_at(p, valid_rows, valid_cols) if os.path.exists(p) else np.full(n_valid, np.nan, np.float32)
    _cache.clear()

dens_year = YEAR if YEAR <= 2024 else 2024
road_year = 2021 if YEAR == 2021 else (2025 if YEAR == 2025 else 2022)
for p, col in [
    (NAS + rf'\people_density\output\04density_aligned\people_density_{dens_year}_04_epsg5179_500m.tif', 'pop_density'),
    (NAS + rf'\cropland\cropland_ratio_{YEAR}_500m.tif', 'cropland'),
    (NAS + rf'\settlement\distance_to_settlement_{YEAR}_500m.tif', 'settlement_dist'),
    (NAS + rf'\road_distance\road_length_density_aligned\road_length_density_{road_year}_500m.tif', 'road_density'),
]:
    feat[col] = read_at(p, valid_rows, valid_cols) if os.path.exists(p) else np.full(n_valid, np.nan, np.float32)
    _cache.clear()
print('  인문환경 완료')

ym  = f'{TARGET_DT.year}{TARGET_DT.month:02d}'
ymd = f'{TARGET_DT.year}{TARGET_DT.month:02d}{TARGET_DT.day:02d}'
prev = TARGET_DT - pd.Timedelta(hours=1)
p_ym, p_ymd, p_hm = f'{prev.year}{prev.month:02d}', f'{prev.year}{prev.month:02d}{prev.day:02d}', f'{prev.hour:02d}00'

for p, col in [
    (NAS + rf'\humidity_4day\{ym}\hm_4day_{ymd}.tif', 'hum4d'),
    (NAS + rf'\precip_4day_masked\{ym}\rn_4day_{ymd}.tif', 'prcp4d'),
    (NAS + rf'\vpd_moedel2\{p_ym}\vpd_{p_ymd}_{p_hm}.tif', 'vpd'),
    (NAS + rf'\wind_model2\{p_ym}\wind_speed_{p_ymd}_{p_hm}.tif', 'wind'),
]:
    feat[col] = read_at(p, valid_rows, valid_cols) if os.path.exists(p) else np.full(n_valid, np.nan, np.float32)
    _cache.clear()

# NDVI/NDMI — 구름 결측을 이전 합성본으로 순차 보완 (원본 스크립트와 동일)
ndvi_files = sorted(glob.glob(NAS + r'\mod09a1_ndvi\*\mod_ndvi_*.tif'))
ndmi_files = sorted(glob.glob(NAS + r'\mod09a1_ndmi\*\mod_ndmi_*.tif'))
ndvi_dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndvi_files]
ndmi_dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndmi_files]

for files, dates, col in [(ndvi_files, ndvi_dates, 'ndvi'), (ndmi_files, ndmi_dates, 'ndmi')]:
    before = sorted([d for d in dates if d + pd.Timedelta(days=8) <= TARGET_DT], reverse=True)
    out = np.full(n_valid, np.nan, dtype=np.float32)
    for d in before[:5]:
        vals = read_at(files[dates.index(d)], valid_rows, valid_cols)
        _cache.clear()
        m = np.isnan(out)
        out[m] = vals[m]
        if not np.isnan(out).any():
            break
    feat[col] = out

doy = TARGET_DT.dayofyear
feat['doy_sin'] = np.full(n_valid, np.sin(2 * np.pi * doy / 365), np.float32)
feat['doy_cos'] = np.full(n_valid, np.cos(2 * np.pi * doy / 365), np.float32)
print('  동적 피처 완료')

FEATURE_COLS = [
    'dem', 'slope', 'asp_cos', 'asp_sin', 'twi',
    'lc_urban', 'lc_deciduous', 'lc_conifer', 'lc_mixed_forest', 'lc_grass', 'lc_water',
    'pop_density', 'cropland', 'settlement_dist', 'road_density',
    'hum4d', 'prcp4d', 'vpd', 'wind', 'ndvi', 'ndmi', 'doy_sin', 'doy_cos',
]
X = np.column_stack([feat[c] for c in FEATURE_COLS]).astype(np.float32)
valid_mask = ~np.isnan(X).any(axis=1)
print(f'입력 결측 제외 후 유효: {valid_mask.sum():,} / {n_valid:,}')

lgbm = joblib.load(NAS + rf'\ml_results\exp_no_smap_spi_temp_4v1\lgbm_models\lgbm_fold5_test{FOLD_YEAR}.pkl')
P_lgbm = np.full(n_valid, np.nan, np.float32)
P_lgbm[valid_mask] = lgbm.predict_proba(X[valid_mask])[:, 1]
print(f'P_lgbm 평균 {np.nanmean(P_lgbm):.4f}  최대 {np.nanmax(P_lgbm):.4f}')

# ── Stage2 12h 시퀀스 ────────────────────────────────────────────────
print('\n[Stage2] 12h 시퀀스 추출 중...')
seq_vpd  = np.full((n_valid, 12), np.nan, np.float32)
seq_wind = np.full((n_valid, 12), np.nan, np.float32)
seq_vpd[:, 11], seq_wind[:, 11] = feat['vpd'], feat['wind']

for lag in range(1, 12):
    rdt = TARGET_DT - pd.Timedelta(hours=lag + 1)
    a, b, c = f'{rdt.year}{rdt.month:02d}', f'{rdt.year}{rdt.month:02d}{rdt.day:02d}', f'{rdt.hour:02d}00'
    idx = 11 - lag
    pv = NAS + rf'\vpd_moedel2\{a}\vpd_{b}_{c}.tif'
    pw = NAS + rf'\wind_model2\{a}\wind_speed_{b}_{c}.tif'
    if os.path.exists(pv):
        seq_vpd[:, idx] = read_at(pv, valid_rows, valid_cols); _cache.clear()
    if os.path.exists(pw):
        seq_wind[:, idx] = read_at(pw, valid_rows, valid_cols); _cache.clear()

for lag in range(11):
    m = np.isnan(seq_vpd[:, lag]);  seq_vpd[m, lag]  = seq_vpd[m, 11]
    m = np.isnan(seq_wind[:, lag]); seq_wind[m, lag] = seq_wind[m, 11]
seq_vpd  = np.nan_to_num(seq_vpd, nan=0.0)
seq_wind = np.nan_to_num(seq_wind, nan=0.0)
print('시퀀스 완료')

# ── 스케일링 + 추론 ──────────────────────────────────────────────────
seq_full = np.stack([seq_vpd, seq_wind], axis=-1)
static_full = np.column_stack([
    np.nan_to_num(P_lgbm, nan=0.0),
    np.nan_to_num(feat['ndvi'], nan=0.0),
    np.nan_to_num(feat['ndmi'], nan=0.0),
    np.nan_to_num(feat['hum4d'], nan=0.0),
    np.nan_to_num(feat['prcp4d'], nan=0.0),
    feat['doy_sin'], feat['doy_cos'],
]).astype(np.float32)

with open(os.path.join(MDL_DIR, 'gru_ign_fold5_test2025_scaler.pkl'), 'rb') as f:
    scaler = pickle.load(f)
allf = np.concatenate([seq_full.reshape(n_valid, -1), static_full], axis=1)
alls = scaler.transform(allf)
seq_scaled    = alls[:, :24].reshape(n_valid, 12, 2).astype(np.float32)
static_scaled = alls[:, 24:].astype(np.float32)

N_OUT = len(HORIZONS)
gru_enc = nn.GRU(2, 64, num_layers=2, batch_first=True, dropout=0.3)
gru_head = nn.Sequential(nn.Linear(64 + 7, 64), nn.ReLU(), nn.Dropout(0.3),
                         nn.Linear(64, N_OUT), nn.Sigmoid())
gru_enc.load_state_dict(torch.load(os.path.join(MDL_DIR, 'gru_ign_fold5_test2025_body.pt'), map_location='cpu'))
gru_head.load_state_dict(torch.load(os.path.join(MDL_DIR, 'gru_ign_fold5_test2025_head.pt'), map_location='cpu'))
gru_enc.eval(); gru_head.eval()

probs = np.zeros((n_valid, N_OUT), np.float32)
with torch.no_grad():
    for i in range(0, n_valid, 8192):
        s  = torch.tensor(seq_scaled[i:i+8192])
        st = torch.tensor(static_scaled[i:i+8192])
        out, _ = gru_enc(s)
        probs[i:i+8192] = gru_head(torch.cat([out[:, -1, :], st], 1)).numpy()

probs[~valid_mask] = np.nan
for k, H in enumerate(HORIZONS):
    v = probs[:, k]
    print(f't+{H}h: 평균={np.nanmean(v):.5f} 최대={np.nanmax(v):.4f} '
          f'상위1%={np.nanpercentile(v,99):.4f} 상위0.1%={np.nanpercentile(v,99.9):.4f}')

out_df = pd.DataFrame({'prow': valid_rows.astype(np.int32),
                       'pcol': valid_cols.astype(np.int32),
                       'P_lgbm': P_lgbm})
for k, H in enumerate(HORIZONS):
    out_df[f'y_prob_t{H}'] = probs[:, k]

stamp = TARGET_DT.strftime('%Y%m%d_%H%M')
out_path = os.path.join(OUT_DIR, f'hazard_ignition_{stamp}.parquet')
out_df.to_parquet(out_path, index=False)
print(f'\n저장: {out_path}  ({(time.time()-t0)/60:.1f}분)')
