"""
발화직전(pre-ignition) 마커 행 + hard negative 생성.

배경: 20_label_audit_new_ignition.py 결과, 기존 seq_dataset_12h_multih_4v1의
      label_t1/t2/t3 양성은 100%가 "t 시점에 이미 화재 중"이었다.
      전국 무작위 배경 샘플링만으로는 "발화 3시간 전 그 픽셀"이 뽑힐 확률이 0에 가깝기 때문.
      → model2/docs/README.md의 설계대로 발화직전 마커를 명시적으로 추가한다.

기존 09~12 전체(518,819행) 재구축이 아니라, 아래 행만 새로 만든다.
  - pre_ignition : 화재 1,360건 × 발화시각 T-1h/T-2h/T-3h  (최대 4,080행)
  - hard_neg     : 각 마커와 같은 시각, 발화픽셀 반경 1~10km 안의 미발화 픽셀
                   (마커당 최대 HARD_NEG_PER_MARKER개)

라벨은 기존 GRU(멀티horizon 동시출력) 구조를 그대로 유지하도록 horizon별로 부여한다.
  - T-1h 마커 → label_t1=1, label_t2=0, label_t3=0
  - T-2h 마커 → label_t2=1
  - T-3h 마커 → label_t3=1
  - hard_neg  → 모두 0

출력 스키마는 seq_dataset_12h_multih_4v1.parquet와 동일(GRU가 쓰는 컬럼 기준).
피처 추출 규칙은 09_build_dataset_4v1.py / 12_build_seq_dataset_4v1.py와 1:1로 맞춘다.

I/O 주의: 일별·시간별 래스터는 전체 배열(약 18MB)을 읽지 않고 윈도우 단위로
          필요한 픽셀만 읽는다. 마커 행이 적어 전체 읽기는 수십 GB 낭비가 된다.
"""

import os, glob, re, time
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.transform import rowcol
from concurrent.futures import ThreadPoolExecutor
import pyproj

# "def 금지" 규칙의 예외: NAS(SMB) 래스터 점 추출은 파일 열기 왕복지연이 병목이라
# 스레드 병렬화가 필수이고, ThreadPoolExecutor는 호출 가능 객체를 요구한다.
# (model2 README에서 nn.Module을 예외로 둔 것과 같은 취지)
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

LOOKBACK             = 12
HORIZONS             = [1, 2, 3]
HARD_NEG_PER_MARKER  = 5
RADIUS_MIN_PIX       = 2     # 1km  / 500m
RADIUS_MAX_PIX       = 20    # 10km / 500m
DATA_CAP_2025        = pd.Timestamp('2025-06-26 06:00:00')
SEED                 = 42

os.makedirs(OUT_DIR, exist_ok=True)
rng = np.random.default_rng(SEED)
t0 = time.time()

# ── 마스크 ───────────────────────────────────────────────────────────
with rasterio.open(MASK_PATH) as src:
    mask_arr  = src.read(1)
    transform = src.transform
    shape     = (src.height, src.width)
valid_rows, valid_cols = np.where(mask_arr == 1)
print(f'유효 픽셀: {len(valid_rows):,}개 | 격자: {shape}')

# ── 화재 사건 (09_build_dataset_4v1.py와 동일 필터) ──────────────────
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
geo = geo[
    (geo['start_dt'].dt.year.between(2021, 2025)) &
    (geo['start_dt'].dt.month.isin([2, 3, 4, 5, 6])) &
    geo['lon'].notna() &
    (geo['end_dt'] > geo['start_dt']) &
    ((geo['end_dt'] - geo['start_dt']).dt.total_seconds() <= 72 * 3600)
].copy()

transformer = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)
xs, ys = transformer.transform(geo['lon'].values, geo['lat'].values)
rs = np.array([rowcol(transform, x, y)[0] for x, y in zip(xs, ys)])
cs = np.array([rowcol(transform, x, y)[1] for x, y in zip(xs, ys)])
geo['prow'] = rs
geo['pcol'] = cs
geo = geo[(geo['prow'] >= 0) & (geo['prow'] < shape[0]) &
          (geo['pcol'] >= 0) & (geo['pcol'] < shape[1])].copy()
geo['ignite_h'] = geo['start_dt'].dt.floor('h')
print(f'대상 화재: {len(geo)}건  (README 기준 1,360건과 대조)')

# 스모크 테스트용 — LIMIT_EVENTS 환경변수가 있으면 앞 N건만 처리
_limit = int(os.environ.get('LIMIT_EVENTS', '0'))
if _limit > 0:
    geo = geo.head(_limit).copy()
    print(f'[LIMIT_EVENTS={_limit}] 스모크 테스트 모드 — {len(geo)}건만 처리')

# ── 화재 진행중(fire_active) 시공간 집합 — 마커 제외용 ────────────────
fire_active = set()
for r in geo.itertuples():
    for h in pd.date_range(r.start_dt.floor('h'), r.end_dt.floor('h'), freq='h'):
        if h.month in [2, 3, 4, 5, 6] and not (h.year == 2025 and h > DATA_CAP_2025):
            fire_active.add((int(r.prow), int(r.pcol), h))
print(f'화재 진행중 시공간 포인트: {len(fire_active):,}개')

ignite_set = set(zip(geo['prow'].astype(int), geo['pcol'].astype(int), geo['ignite_h']))

# ── 마커 + hard negative 행 구성 ─────────────────────────────────────
rows = []
n_skip_active, n_skip_season = 0, 0

for r in geo.itertuples():
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

        # hard negative — 같은 시각, 반경 1~10km 링 안의 미발화 유효픽셀
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

# ── 정적 피처 (파일 수가 적어 전체 읽기) ─────────────────────────────
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

# ── 연도별 피처 ──────────────────────────────────────────────────────
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
            df.loc[idx, f'lc_{lc}'] = arr[r, c]
            del arr
        else:
            df.loc[idx, f'lc_{lc}'] = np.nan

    dens_year = year if year <= 2024 else 2024
    path = NAS + rf'\people_density\output\04density_aligned\people_density_{dens_year}_04_epsg5179_500m.tif'
    if os.path.exists(path):
        with rasterio.open(path) as s:
            arr = s.read(1).astype(np.float32); nd = s.nodata
        if nd is not None: arr[arr == nd] = np.nan
        df.loc[idx, 'pop_density'] = arr[r, c]
        del arr

    path = NAS + rf'\cropland\cropland_ratio_{year}_500m.tif'
    if os.path.exists(path):
        with rasterio.open(path) as s:
            arr = s.read(1).astype(np.float32); nd = s.nodata
        if nd is not None: arr[arr == nd] = np.nan
        df.loc[idx, 'cropland'] = arr[r, c]
        del arr

    path = NAS + rf'\settlement\distance_to_settlement_{year}_500m.tif'
    if os.path.exists(path):
        with rasterio.open(path) as s:
            arr = s.read(1).astype(np.float32); nd = s.nodata
        if nd is not None: arr[arr == nd] = np.nan
        df.loc[idx, 'settlement_dist'] = arr[r, c]
        del arr

    road_year = 2021 if year == 2021 else (2025 if year == 2025 else 2022)
    path = NAS + rf'\road_distance\road_length_density_aligned\road_length_density_{road_year}_500m.tif'
    if os.path.exists(path):
        with rasterio.open(path) as s:
            arr = s.read(1).astype(np.float32); nd = s.nodata
        if nd is not None: arr[arr == nd] = np.nan
        df.loc[idx, 'road_density'] = arr[r, c]
        del arr

    print(f'  연도 {year} 완료')

# ── NDVI/NDMI (8일 합성, 09번과 동일한 시간유출 방지 규칙) ────────────
ndvi_files = sorted(glob.glob(NAS + r'\mod09a1_ndvi\*\mod_ndvi_*.tif'))
ndmi_files = sorted(glob.glob(NAS + r'\mod09a1_ndmi\*\mod_ndmi_*.tif'))
ndvi_dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndvi_files]
ndmi_dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndmi_files]

# ── NDVI/NDMI · 일별 · 시간별 시퀀스를 하나의 작업목록으로 모아 병렬 추출 ──
# 같은 래스터 파일이 여러 마커의 lookback 창에 중복 등장하므로(예: T-1h 마커와
# T-2h 마커의 12시간 창이 대부분 겹침) 파일 단위로 먼저 합쳐야 열기 횟수가 줄어든다.
# (path, col) → 그 파일에서 읽어야 할 df 행 인덱스 목록
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
        before = [d for d in dates if d + pd.Timedelta(days=8) <= dt]
        if before:
            p = files[dates.index(max(before))]
            need.setdefault((p, col), []).append(idx)

    for path, col in [(NAS + rf'\humidity_4day\{ym}\hm_4day_{ymd}.tif', 'hum4d'),
                      (NAS + rf'\precip_4day_masked\{ym}\rn_4day_{ymd}.tif', 'prcp4d')]:
        need.setdefault((path, col), []).append(idx)

# 12_build_seq_dataset_4v1.py 규칙: vpd_t0 = T-1h 래스터, vpd_tm{lag} = T-(lag+1)h 래스터
for (yr, mo, dy, hr), grp in df.groupby(['year', 'month', 'day', 'hour']):
    dt  = pd.Timestamp(yr, mo, dy, hr)
    idx = grp.index.values
    for lag in range(0, LOOKBACK):
        raster_dt = dt - pd.Timedelta(hours=lag + 1)
        ym  = f'{raster_dt.year}{raster_dt.month:02d}'
        ymd = f'{raster_dt.year}{raster_dt.month:02d}{raster_dt.day:02d}'
        hm  = f'{raster_dt.hour:02d}00'
        suffix = 't0' if lag == 0 else f'tm{lag}'
        for path, col in [(NAS + rf'\vpd_moedel2\{ym}\vpd_{ymd}_{hm}.tif',        f'vpd_{suffix}'),
                          (NAS + rf'\wind_model2\{ym}\wind_speed_{ymd}_{hm}.tif', f'wind_{suffix}')]:
            need.setdefault((path, col), []).append(idx)

keys  = list(need.keys())
idxs  = [np.concatenate(need[k]) for k in keys]
tasks = [(keys[i][0], rows_arr[idxs[i]], cols_arr[idxs[i]]) for i in range(len(keys))]
print(f'\n래스터 점추출 작업: {len(tasks):,}개 파일 (총 {sum(len(x) for x in idxs):,}점)'
      f' — {N_WORKERS}스레드 병렬')

n_missing_file, done = 0, 0
with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    for i, vals in enumerate(ex.map(_sample_points, tasks)):
        if vals is None:
            n_missing_file += 1
        else:
            df.loc[idxs[i], keys[i][1]] = vals
        done += 1
        if done % 2000 == 0:
            print(f'  [{done:,}/{len(tasks):,}] ({(time.time()-t0)/60:.1f}분 경과)')

print(f'점추출 완료 (없거나 손상된 래스터 {n_missing_file:,}건)')

# ── 12번과 동일한 결측 처리 ──────────────────────────────────────────
nan_before = df[seq_cols].isna().any(axis=1).sum()
print(f'시퀀스 결측 샘플: {nan_before:,}개')
for lag in range(1, LOOKBACK):
    df[f'vpd_tm{lag}']  = df[f'vpd_tm{lag}'].fillna(df['vpd_t0'])
    df[f'wind_tm{lag}'] = df[f'wind_tm{lag}'].fillna(df['wind_t0'])
for col in seq_cols:
    df[col] = df[col].fillna(0.0)

# ── 09번 호환: vpd/wind 단일 컬럼(= T-1h) — P_lgbm 입력에 필요 ───────
df['vpd']  = df['vpd_t0']
df['wind'] = df['wind_t0']

df['doy']     = pd.to_datetime(df[['year', 'month', 'day']]).dt.dayofyear
df['doy_sin'] = np.sin(2 * np.pi * df['doy'] / 365)
df['doy_cos'] = np.cos(2 * np.pi * df['doy'] / 365)

out_path = os.path.join(OUT_DIR, 'preignition_markers_raw.parquet')
df.drop(columns=['datetime']).to_parquet(out_path, index=False)

print(f'\n저장: {out_path}')
print(f'shape: {df.shape}  ({(time.time()-t0)/60:.1f}분 소요)')
print(f'sample_type: {df["sample_type"].value_counts().to_dict()}')
print('주요 피처 결측률:')
print(df[['dem', 'lc_conifer', 'pop_density', 'hum4d', 'prcp4d',
          'vpd_t0', 'wind_t0', 'ndvi', 'ndmi']].isnull().mean().round(4).to_string())
