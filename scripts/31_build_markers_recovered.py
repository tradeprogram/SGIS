"""
지오코딩 복구 사건의 발화직전 마커 + hard negative 증분 구축.

21번은 기존 1,360건을 3.5시간에 처리했다. 여기서는 30/30b/30c로 복구된 사건만
증분으로 처리해 기존 마커 파일에 더한다(전체 재구축 대신).

정밀도 필터: recover_level in {ri, dong} 만 사용.
  시군구(sgg) 중심점은 면적이 수백 km²라 500m 격자 위치가 사실상 무작위가 된다.
  기존 데이터가 이미 수용한 최저 정밀도는 eup_myeon(읍면 중심)이므로 리·동은 그와
  동등하거나 더 정밀하지만, 시군구는 그보다 훨씬 거칠어 제외한다.

좌표: SGIS 지오코딩이 EPSG:5179로 직접 반환하므로 재투영 없이 바로 행/열로 변환한다.

fire_active(마커 제외용) 집합은 기존 1,360건 + 복구 사건을 모두 합쳐 구성한다.
그렇지 않으면 복구 사건 시각이 기존 화재 진행 중과 겹쳐도 걸러지지 않는다.
"""

import os, glob, re, time
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.transform import rowcol
from concurrent.futures import ThreadPoolExecutor
import pyproj

N_WORKERS = 16


def _sample_points(task):
    """task = (path, rows, cols) → 해당 래스터에서 (rows, cols) 픽셀값 배열."""
    path, rr, cc = task
    if not os.path.exists(path):
        return None
    try:
        with rasterio.open(path) as s:
            nd = s.nodata
            vals = np.empty(len(rr), dtype=np.float32)
            for i in range(len(rr)):
                vals[i] = s.read(1, window=Window(int(cc[i]), int(rr[i]), 1, 1))[0, 0]
        if nd is not None:
            vals[vals == nd] = np.nan
        return vals
    except Exception as e:
        print(f'  [손상파일 스킵] {path}: {e}')
        return None


NAS       = r'V:\data'
MASK_PATH = NAS + r'\mask\common_mask_500m_5179.tif'
GEO_CSV   = NAS + r'\wildfire_reference\fire_events_geocoded.csv'
RAW_CSV   = NAS + r'\wildfire_reference\fire_raw_2015_2025.csv'
OUT_DIR   = r'C:\for_sgis\data\grid_data\derived'
REC_CSV   = os.path.join(OUT_DIR, 'fire_events_geocode_recovered.csv')
OUT_PATH  = os.path.join(OUT_DIR, 'preignition_markers_recovered.parquet')

LOOKBACK             = 12
HORIZONS             = [1, 2, 3]
HARD_NEG_PER_MARKER  = 5
RADIUS_MIN_PIX       = 2
RADIUS_MAX_PIX       = 20
DATA_CAP_2025        = pd.Timestamp('2025-06-26 06:00:00')
ACCEPT_LEVELS        = ['ri', 'dong']
SEED                 = 42

rng = np.random.default_rng(SEED)
t0 = time.time()

with rasterio.open(MASK_PATH) as src:
    mask_arr  = src.read(1)
    transform = src.transform
    shape     = (src.height, src.width)
print(f'유효 픽셀: {int((mask_arr == 1).sum()):,}개 | 격자: {shape}')

# ── 원 데이터(성공 지오코딩) — fire_active 구성용 ────────────────────
geo = pd.read_csv(GEO_CSV, encoding='utf-8-sig')
raw = pd.read_csv(RAW_CSV, encoding='utf-8-sig')
geo['start_dt'] = pd.to_datetime(geo['datetime'])
raw['end_dt'] = pd.to_datetime(
    raw['endyear'].astype(str) + '-' +
    raw['endmonth'].astype(str).str.zfill(2) + '-' +
    raw['endday'].astype(str).str.zfill(2) + ' ' +
    raw['endtime'].astype(str), errors='coerce'
)
geo['end_dt'] = raw['end_dt'].values

base = geo[
    (geo['start_dt'].dt.year.between(2021, 2025)) &
    (geo['start_dt'].dt.month.isin([2, 3, 4, 5, 6])) &
    geo['lon'].notna() &
    (geo['end_dt'] > geo['start_dt']) &
    ((geo['end_dt'] - geo['start_dt']).dt.total_seconds() <= 72 * 3600)
].copy()
tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)
bx, by = tr.transform(base['lon'].values, base['lat'].values)
base['prow'] = [rowcol(transform, x, y)[0] for x, y in zip(bx, by)]
base['pcol'] = [rowcol(transform, x, y)[1] for x, y in zip(bx, by)]
base = base[(base['prow'] >= 0) & (base['prow'] < shape[0]) &
            (base['pcol'] >= 0) & (base['pcol'] < shape[1])].copy()
print(f'기존 사건(21번 처리분): {len(base)}건')

# ── 복구 사건 ────────────────────────────────────────────────────────
rec = pd.read_csv(REC_CSV, encoding='utf-8-sig')
rec = rec[rec['recover_level'].isin(ACCEPT_LEVELS)].copy()
print(f'복구 사건 (정밀도 {ACCEPT_LEVELS}): {len(rec)}건')

rec = rec.merge(geo[['fire_id', 'start_dt', 'end_dt']], on='fire_id', how='left')
before = len(rec)
rec = rec[(rec['end_dt'] > rec['start_dt']) &
          ((rec['end_dt'] - rec['start_dt']).dt.total_seconds() <= 72 * 3600)].copy()
print(f'  화재지속시간 필터(0<t<=72h): {before} → {len(rec)}건')

rec['prow'] = [rowcol(transform, x, y)[0] for x, y in zip(rec['x_5179'], rec['y_5179'])]
rec['pcol'] = [rowcol(transform, x, y)[1] for x, y in zip(rec['x_5179'], rec['y_5179'])]
before = len(rec)
rec = rec[(rec['prow'] >= 0) & (rec['prow'] < shape[0]) &
          (rec['pcol'] >= 0) & (rec['pcol'] < shape[1])].copy()
rec = rec[mask_arr[rec['prow'].astype(int), rec['pcol'].astype(int)] == 1].copy()
print(f'  격자 범위·유효마스크 필터: {before} → {len(rec)}건')
rec['ignite_h'] = rec['start_dt'].dt.floor('h')

# ── fire_active: 기존 + 복구 전체 ────────────────────────────────────
fire_active = set()
for src_df in (base, rec):
    for r in src_df.itertuples():
        for h in pd.date_range(r.start_dt.floor('h'), r.end_dt.floor('h'), freq='h'):
            if h.month in [2, 3, 4, 5, 6] and not (h.year == 2025 and h > DATA_CAP_2025):
                fire_active.add((int(r.prow), int(r.pcol), h))
print(f'화재 진행중 시공간 포인트(기존+복구): {len(fire_active):,}개')

ignite_set = set()
for src_df in (base.assign(ignite_h=base['start_dt'].dt.floor('h')), rec):
    ignite_set |= set(zip(src_df['prow'].astype(int), src_df['pcol'].astype(int),
                          src_df['ignite_h']))

# ── 마커 + hard negative ─────────────────────────────────────────────
rows = []
n_skip_active, n_skip_season = 0, 0
for r in rec.itertuples():
    pr, pc = int(r.prow), int(r.pcol)
    for k in HORIZONS:
        m_dt = r.ignite_h - pd.Timedelta(hours=k)
        if m_dt.month not in [2, 3, 4, 5, 6]:
            n_skip_season += 1
            continue
        if m_dt.year == 2025 and m_dt > DATA_CAP_2025:
            n_skip_season += 1
            continue
        if (pr, pc, m_dt) in fire_active:
            n_skip_active += 1
            continue

        lab = {f'label_t{H}': 0 for H in HORIZONS}
        lab[f'label_t{k}'] = 1
        rows.append({'prow': pr, 'pcol': pc, 'datetime': m_dt,
                     'sample_type': 'pre_ignition', 'fire_id': int(r.fire_id),
                     'label': 0, **lab})

        got = 0
        for _ in range(HARD_NEG_PER_MARKER * 12):
            if got >= HARD_NEG_PER_MARKER:
                break
            ang = rng.uniform(0, 2 * np.pi)
            rad = rng.uniform(RADIUS_MIN_PIX, RADIUS_MAX_PIX)
            hr_ = int(round(pr + rad * np.sin(ang)))
            hc_ = int(round(pc + rad * np.cos(ang)))
            if not (0 <= hr_ < shape[0] and 0 <= hc_ < shape[1]):
                continue
            if mask_arr[hr_, hc_] != 1:
                continue
            if (hr_, hc_, m_dt) in fire_active:
                continue
            if any((hr_, hc_, m_dt + pd.Timedelta(hours=H)) in ignite_set for H in HORIZONS):
                continue
            rows.append({'prow': hr_, 'pcol': hc_, 'datetime': m_dt,
                         'sample_type': 'hard_neg', 'fire_id': int(r.fire_id),
                         'label': 0, **{f'label_t{H}': 0 for H in HORIZONS}})
            got += 1

df = pd.DataFrame(rows).drop_duplicates(['prow', 'pcol', 'datetime']).reset_index(drop=True)
df['year']  = df['datetime'].dt.year
df['month'] = df['datetime'].dt.month
df['day']   = df['datetime'].dt.day
df['hour']  = df['datetime'].dt.hour
print(f'\n마커 제외: 시즌밖/데이터캡 {n_skip_season}건, 화재진행중 중복 {n_skip_active}건')
print(f'생성 행: {len(df):,}  {df["sample_type"].value_counts().to_dict()}')
for H in HORIZONS:
    print(f'  label_t{H}=1: {int(df[f"label_t{H}"].sum()):,}')

rows_arr = df['prow'].values
cols_arr = df['pcol'].values

# ── 정적 피처 ────────────────────────────────────────────────────────
static_files = {
    'dem':     NAS + r'\DEM\500m_aligned\dem_500m_5179.tif',
    'slope':   NAS + r'\DEM\500m_aligned\slope_500m_5179.tif',
    'asp_cos': NAS + r'\DEM\500m_aligned\aspect_500m_cos_5179.tif',
    'asp_sin': NAS + r'\DEM\500m_aligned\aspect_500m_sin_5179.tif',
    'twi':     NAS + r'\DEM\500m_aligned\twi_500m_5179.tif',
}
for name, path in static_files.items():
    with rasterio.open(path) as s:
        arr = s.read(1).astype(np.float32); nd = s.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    df[name] = arr[rows_arr, cols_arr]
    del arr
print('정적 피처 완료')

lc_names = ['urban', 'deciduous', 'conifer', 'mixed_forest', 'grass', 'water']
for year in sorted(df['year'].unique()):
    idx = df['year'] == year
    r, c = rows_arr[idx.values], cols_arr[idx.values]
    for lc in lc_names:
        path = NAS + rf'\landcover_raster\landcover_{lc}_ratio_{year}.tif'
        if os.path.exists(path):
            with rasterio.open(path) as s:
                arr = s.read(1).astype(np.float32); nd = s.nodata
            if nd is not None: arr[arr == nd] = np.nan
            df.loc[idx, f'lc_{lc}'] = arr[r, c]; del arr
        else:
            df.loc[idx, f'lc_{lc}'] = np.nan

    dens_year = year if year <= 2024 else 2024
    for path, col in [
        (NAS + rf'\people_density\output\04density_aligned\people_density_{dens_year}_04_epsg5179_500m.tif', 'pop_density'),
        (NAS + rf'\cropland\cropland_ratio_{year}_500m.tif', 'cropland'),
        (NAS + rf'\settlement\distance_to_settlement_{year}_500m.tif', 'settlement_dist'),
    ]:
        if os.path.exists(path):
            with rasterio.open(path) as s:
                arr = s.read(1).astype(np.float32); nd = s.nodata
            if nd is not None: arr[arr == nd] = np.nan
            df.loc[idx, col] = arr[r, c]; del arr

    road_year = 2021 if year == 2021 else (2025 if year == 2025 else 2022)
    path = NAS + rf'\road_distance\road_length_density_aligned\road_length_density_{road_year}_500m.tif'
    if os.path.exists(path):
        with rasterio.open(path) as s:
            arr = s.read(1).astype(np.float32); nd = s.nodata
        if nd is not None: arr[arr == nd] = np.nan
        df.loc[idx, 'road_density'] = arr[r, c]; del arr
    print(f'  연도 {year} 완료')

ndvi_files = sorted(glob.glob(NAS + r'\mod09a1_ndvi\*\mod_ndvi_*.tif'))
ndmi_files = sorted(glob.glob(NAS + r'\mod09a1_ndmi\*\mod_ndmi_*.tif'))
ndvi_dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndvi_files]
ndmi_dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndmi_files]

seq_cols = []
for lag in range(LOOKBACK - 1, 0, -1):
    seq_cols += [f'vpd_tm{lag}', f'wind_tm{lag}']
seq_cols += ['vpd_t0', 'wind_t0']
for col in seq_cols + ['ndvi', 'ndmi', 'hum4d', 'prcp4d']:
    df[col] = np.nan

need = {}
for (yr, mo, dy), grp in df.groupby(['year', 'month', 'day']):
    dt  = pd.Timestamp(yr, mo, dy)
    idx = grp.index.values
    ym  = f'{yr}{mo:02d}'
    ymd = f'{yr}{mo:02d}{dy:02d}'
    for files, dates, col in [(ndvi_files, ndvi_dates, 'ndvi'), (ndmi_files, ndmi_dates, 'ndmi')]:
        before_l = [d for d in dates if d + pd.Timedelta(days=8) <= dt]
        if before_l:
            need.setdefault((files[dates.index(max(before_l))], col), []).append(idx)
    for path, col in [(NAS + rf'\humidity_4day\{ym}\hm_4day_{ymd}.tif', 'hum4d'),
                      (NAS + rf'\precip_4day_masked\{ym}\rn_4day_{ymd}.tif', 'prcp4d')]:
        need.setdefault((path, col), []).append(idx)

for (yr, mo, dy, hr), grp in df.groupby(['year', 'month', 'day', 'hour']):
    dt  = pd.Timestamp(yr, mo, dy, hr)
    idx = grp.index.values
    for lag in range(0, LOOKBACK):
        rdt = dt - pd.Timedelta(hours=lag + 1)
        ym  = f'{rdt.year}{rdt.month:02d}'
        ymd = f'{rdt.year}{rdt.month:02d}{rdt.day:02d}'
        hm  = f'{rdt.hour:02d}00'
        sfx = 't0' if lag == 0 else f'tm{lag}'
        for path, col in [(NAS + rf'\vpd_moedel2\{ym}\vpd_{ymd}_{hm}.tif',        f'vpd_{sfx}'),
                          (NAS + rf'\wind_model2\{ym}\wind_speed_{ymd}_{hm}.tif', f'wind_{sfx}')]:
            need.setdefault((path, col), []).append(idx)

keys  = list(need.keys())
idxs  = [np.concatenate(need[k]) for k in keys]
tasks = [(keys[i][0], rows_arr[idxs[i]], cols_arr[idxs[i]]) for i in range(len(keys))]
print(f'\n래스터 점추출 작업: {len(tasks):,}개 파일 (총 {sum(len(x) for x in idxs):,}점) — {N_WORKERS}스레드')

n_missing, done = 0, 0
with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    for i, vals in enumerate(ex.map(_sample_points, tasks)):
        if vals is None:
            n_missing += 1
        else:
            df.loc[idxs[i], keys[i][1]] = vals
        done += 1
        if done % 2000 == 0:
            print(f'  [{done:,}/{len(tasks):,}] ({(time.time()-t0)/60:.1f}분 경과)')
print(f'점추출 완료 (없거나 손상된 래스터 {n_missing:,}건)')

nan_before = df[seq_cols].isna().any(axis=1).sum()
print(f'시퀀스 결측 샘플: {nan_before:,}개')
for lag in range(1, LOOKBACK):
    df[f'vpd_tm{lag}']  = df[f'vpd_tm{lag}'].fillna(df['vpd_t0'])
    df[f'wind_tm{lag}'] = df[f'wind_tm{lag}'].fillna(df['wind_t0'])
for col in seq_cols:
    df[col] = df[col].fillna(0.0)

df['vpd']  = df['vpd_t0']
df['wind'] = df['wind_t0']
df['doy']     = pd.to_datetime(df[['year', 'month', 'day']]).dt.dayofyear
df['doy_sin'] = np.sin(2 * np.pi * df['doy'] / 365)
df['doy_cos'] = np.cos(2 * np.pi * df['doy'] / 365)

df.drop(columns=['datetime']).to_parquet(OUT_PATH, index=False)
print(f'\n저장: {OUT_PATH}')
print(f'shape: {df.shape}  ({(time.time()-t0)/60:.1f}분 소요)')
print(f'sample_type: {df["sample_type"].value_counts().to_dict()}')
print('주요 피처 결측률:')
print(df[['dem', 'lc_conifer', 'pop_density', 'hum4d', 'prcp4d',
          'vpd_t0', 'wind_t0', 'ndvi', 'ndmi']].isnull().mean().round(4).to_string())
