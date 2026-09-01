"""
발화직전 마커 보충 — 40번(신규 방법론) 사건 목록 기준으로 빠진 것만 만든다.

왜 필요한가
  21번은 옛 09번을 따라 화재 지속시간 72시간 상한을 걸었다. 그 결과
  울진(222.7h)·산청(532.6h)·강릉옥계(155.9h) 같은 실제 대형산불이 통째로 빠졌다.
  신규 방법론은 720시간 상한을 쓰고 SGIS 지오코딩 복구분도 포함하므로
  사건이 1,359 → 1,734건으로 늘어난다. 그중 383건에 마커가 없다.
  빠진 383건의 신고 피해면적 합계가 30,466ha다.

  즉 지금 모델은 기록상 최대 산불들의 "발화 직전"을 한 번도 본 적이 없다.

무엇을 만드는가
  40번이 확정한 사건 목록(fire_cell_summary.csv)에서 마커가 없는 fire_id만 골라
  발화 T-1h / T-2h / T-3h 시점의 피처와 12시간 시퀀스를 뽑는다.
  하드네거티브도 같은 시각·반경 1~10km로 함께 만든다(21번과 동일 규칙).

발화 지점
  폴리곤 보유 화재라도 마커는 폴리곤 전체가 아니라 발화점 1픽셀에 둔다.
  "발화 직전"은 불이 시작된 자리의 조건이지 나중에 탄 자리의 조건이 아니다.

입력은 전부 읽기 전용. NAS 원본은 건드리지 않는다.
출력  derived/preignition_markers_topup.parquet
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
REF       = r'C:\for_sgis\data\fire_reference'
DERIVED   = r'C:\for_sgis\data\grid_data\derived'
MASK      = NAS + r'\mask\common_mask_500m_5179.tif'
HAVE      = os.path.join(DERIVED, 'preignition_markers_raw.parquet')
OUT_PATH  = os.path.join(DERIVED, 'preignition_markers_topup.parquet')

LOOKBACK            = 12
HORIZONS            = [1, 2, 3]
HARD_NEG_PER_MARKER = 5
RADIUS_MIN_PIX      = 2      # 1km
RADIUS_MAX_PIX      = 20     # 10km
MONTHS              = [2, 3, 4, 5, 6]
DATA_CAP_2025       = pd.Timestamp('2025-06-26 06:00:00')
SEED                = 42

rng = np.random.default_rng(SEED)
t0 = time.time()

with rasterio.open(MASK) as src:
    mask_arr, transform = src.read(1), src.transform
    shape = (src.height, src.width)
print(f'격자 {shape}  유효 {int((mask_arr == 1).sum()):,}셀')

# ── 40번이 확정한 사건 목록 ──────────────────────────────────────────
summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
summ['ignite_h'] = pd.to_datetime(summ['ignite_h'])
summ['end_h'] = pd.to_datetime(summ['end_h'])

have_ids = set()
if os.path.exists(HAVE):
    h = pd.read_parquet(HAVE, columns=['fire_id', 'sample_type'])
    have_ids = set(h.loc[h['sample_type'] == 'pre_ignition', 'fire_id'].unique())
todo = summ[~summ['fire_id'].isin(have_ids)].copy()
print(f'사건 {len(summ):,}건 중 마커 없음 {len(todo):,}건 '
      f'(피해면적 합계 {todo["damagearea"].sum():,.0f}ha)')
if len(todo) == 0:
    raise SystemExit('보충할 사건이 없습니다.')

# ── 발화점 좌표 (원본 + SGIS 복구) ───────────────────────────────────
geo = pd.read_csv(os.path.join(REF, 'fire_events_geocoded.csv'), encoding='utf-8-sig')
geo['start_dt'] = pd.to_datetime(geo['datetime'])
tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)
gx, gy = tr.transform(geo['lon'].values, geo['lat'].values)
geo['x_5179'], geo['y_5179'] = gx, gy

rec_path = os.path.join(DERIVED, 'fire_events_geocode_recovered.csv')
if os.path.exists(rec_path):
    rec = pd.read_csv(rec_path, encoding='utf-8-sig')
    rec = rec[rec['recover_level'].isin(['ri', 'dong'])]
    m = geo['x_5179'].isna()
    fill = geo.loc[m, ['fire_id']].merge(rec[['fire_id', 'x_5179', 'y_5179']],
                                         on='fire_id', how='left')
    geo.loc[m, 'x_5179'] = fill['x_5179'].values
    geo.loc[m, 'y_5179'] = fill['y_5179'].values

todo = todo.merge(geo[['fire_id', 'x_5179', 'y_5179']], on='fire_id', how='left')
todo = todo[todo['x_5179'].notna()].copy()
todo['prow'] = [rowcol(transform, x, y)[0] for x, y in zip(todo['x_5179'], todo['y_5179'])]
todo['pcol'] = [rowcol(transform, x, y)[1] for x, y in zip(todo['x_5179'], todo['y_5179'])]
todo = todo[(todo['prow'] >= 0) & (todo['prow'] < shape[0]) &
            (todo['pcol'] >= 0) & (todo['pcol'] < shape[1])].copy()
todo = todo[mask_arr[todo['prow'].astype(int), todo['pcol'].astype(int)] == 1].copy()
print(f'좌표·격자 확보: {len(todo):,}건')

# ── 화재 진행중 집합 (전 사건) — 마커 제외용 ─────────────────────────
cells = pd.read_parquet(os.path.join(DERIVED, 'fire_cells.parquet'))
cells['ignite_h'] = pd.to_datetime(cells['ignite_h'])
cells['end_h'] = pd.to_datetime(cells['end_h'])
fire_active = set()
for r in cells.itertuples():
    for h in pd.date_range(r.ignite_h, r.end_h, freq='h'):
        if h.month in MONTHS and not (h.year == 2025 and h > DATA_CAP_2025):
            fire_active.add((int(r.prow), int(r.pcol), h))
print(f'화재 진행중 시공간 포인트: {len(fire_active):,}개')

ignite_set = set()
for r in summ.merge(geo[['fire_id', 'x_5179', 'y_5179']], on='fire_id', how='left').itertuples():
    if pd.isna(r.x_5179):
        continue
    pr, pc = rowcol(transform, r.x_5179, r.y_5179)
    if 0 <= pr < shape[0] and 0 <= pc < shape[1]:
        ignite_set.add((int(pr), int(pc), r.ignite_h))

# ── 마커 + 하드네거티브 ──────────────────────────────────────────────
rows = []
n_skip = 0
for r in todo.itertuples():
    pr, pc = int(r.prow), int(r.pcol)
    for k in HORIZONS:
        m_dt = r.ignite_h - pd.Timedelta(hours=k)
        if m_dt.month not in MONTHS or (m_dt.year == 2025 and m_dt > DATA_CAP_2025) \
           or (pr, pc, m_dt) in fire_active:
            n_skip += 1
            continue
        lab = {f'label_t{H}': 0 for H in HORIZONS}
        lab[f'label_t{k}'] = 1
        rows.append({'prow': pr, 'pcol': pc, 'datetime': m_dt, 'sample_type': 'pre_ignition',
                     'fire_id': int(r.fire_id), 'label': 0, **lab})

        got = 0
        for _ in range(HARD_NEG_PER_MARKER * 12):
            if got >= HARD_NEG_PER_MARKER:
                break
            ang = rng.uniform(0, 2 * np.pi)
            rad = rng.uniform(RADIUS_MIN_PIX, RADIUS_MAX_PIX)
            hr_ = int(round(pr + rad * np.sin(ang)))
            hc_ = int(round(pc + rad * np.cos(ang)))
            if not (0 <= hr_ < shape[0] and 0 <= hc_ < shape[1]) or mask_arr[hr_, hc_] != 1:
                continue
            if (hr_, hc_, m_dt) in fire_active:
                continue
            if any((hr_, hc_, m_dt + pd.Timedelta(hours=H)) in ignite_set for H in HORIZONS):
                continue
            rows.append({'prow': hr_, 'pcol': hc_, 'datetime': m_dt, 'sample_type': 'hard_neg',
                         'fire_id': int(r.fire_id), 'label': 0,
                         **{f'label_t{H}': 0 for H in HORIZONS}})
            got += 1

df = pd.DataFrame(rows).drop_duplicates(['prow', 'pcol', 'datetime']).reset_index(drop=True)
df['year'], df['month'] = df['datetime'].dt.year, df['datetime'].dt.month
df['day'], df['hour'] = df['datetime'].dt.day, df['datetime'].dt.hour
print(f'\n마커 제외 {n_skip}건 → 생성 {len(df):,}행 {df["sample_type"].value_counts().to_dict()}')
for H in HORIZONS:
    print(f'  label_t{H}=1: {int(df[f"label_t{H}"].sum()):,}')

rows_arr, cols_arr = df['prow'].values, df['pcol'].values

# ── 정적·연도별 피처 ─────────────────────────────────────────────────
for name, path in {
    'dem': NAS + r'\DEM\500m_aligned\dem_500m_5179.tif',
    'slope': NAS + r'\DEM\500m_aligned\slope_500m_5179.tif',
    'asp_cos': NAS + r'\DEM\500m_aligned\aspect_500m_cos_5179.tif',
    'asp_sin': NAS + r'\DEM\500m_aligned\aspect_500m_sin_5179.tif',
    'twi': NAS + r'\DEM\500m_aligned\twi_500m_5179.tif',
}.items():
    with rasterio.open(path) as s:
        arr = s.read(1).astype(np.float32); nd = s.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    df[name] = arr[rows_arr, cols_arr]
    del arr
print('정적 피처 완료')

for year in sorted(df['year'].unique()):
    idx = df['year'] == year
    r, c = rows_arr[idx.values], cols_arr[idx.values]
    for lc in ['urban', 'deciduous', 'conifer', 'mixed_forest', 'grass', 'water']:
        p = NAS + rf'\landcover_raster\landcover_{lc}_ratio_{year}.tif'
        if os.path.exists(p):
            with rasterio.open(p) as s:
                arr = s.read(1).astype(np.float32); nd = s.nodata
            if nd is not None: arr[arr == nd] = np.nan
            df.loc[idx, f'lc_{lc}'] = arr[r, c]; del arr
        else:
            df.loc[idx, f'lc_{lc}'] = np.nan
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
            df.loc[idx, col] = arr[r, c]; del arr
    print(f'  연도 {year} 완료')

# ── NDVI/NDMI · 일별 · 시간별 시퀀스 (파일 단위로 묶어 병렬 추출) ─────
ndvi_f = sorted(glob.glob(NAS + r'\mod09a1_ndvi\*\mod_ndvi_*.tif'))
ndmi_f = sorted(glob.glob(NAS + r'\mod09a1_ndmi\*\mod_ndmi_*.tif'))
ndvi_d = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndvi_f]
ndmi_d = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndmi_f]

seq_cols = []
for lag in range(LOOKBACK - 1, 0, -1):
    seq_cols += [f'vpd_tm{lag}', f'wind_tm{lag}']
seq_cols += ['vpd_t0', 'wind_t0']
for col in seq_cols + ['ndvi', 'ndmi', 'hum4d', 'prcp4d']:
    df[col] = np.nan

need = {}
for (yr, mo, dy), grp in df.groupby(['year', 'month', 'day']):
    dt, idx = pd.Timestamp(yr, mo, dy), grp.index.values
    ym, ymd = f'{yr}{mo:02d}', f'{yr}{mo:02d}{dy:02d}'
    for files, dates, col in [(ndvi_f, ndvi_d, 'ndvi'), (ndmi_f, ndmi_d, 'ndmi')]:
        before = [d for d in dates if d + pd.Timedelta(days=8) <= dt]
        if before:
            need.setdefault((files[dates.index(max(before))], col), []).append(idx)
    for p, col in [(NAS + rf'\humidity_4day\{ym}\hm_4day_{ymd}.tif', 'hum4d'),
                   (NAS + rf'\precip_4day_masked\{ym}\rn_4day_{ymd}.tif', 'prcp4d')]:
        need.setdefault((p, col), []).append(idx)

for (yr, mo, dy, hr), grp in df.groupby(['year', 'month', 'day', 'hour']):
    dt, idx = pd.Timestamp(yr, mo, dy, hr), grp.index.values
    for lag in range(0, LOOKBACK):
        rdt = dt - pd.Timedelta(hours=lag + 1)
        ym, ymd, hm = rdt.strftime('%Y%m'), rdt.strftime('%Y%m%d'), f'{rdt.hour:02d}00'
        sfx = 't0' if lag == 0 else f'tm{lag}'
        for p, col in [(NAS + rf'\vpd_moedel2\{ym}\vpd_{ymd}_{hm}.tif', f'vpd_{sfx}'),
                       (NAS + rf'\wind_model2\{ym}\wind_speed_{ymd}_{hm}.tif', f'wind_{sfx}')]:
            need.setdefault((p, col), []).append(idx)

keys = list(need.keys())
idxs = [np.concatenate(need[k]) for k in keys]
tasks = [(keys[i][0], rows_arr[idxs[i]], cols_arr[idxs[i]]) for i in range(len(keys))]
print(f'\n래스터 점추출: {len(tasks):,}개 파일 / {sum(len(x) for x in idxs):,}점 — {N_WORKERS}스레드')

n_missing, done = 0, 0
with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    for i, vals in enumerate(ex.map(_sample_points, tasks)):
        if vals is None:
            n_missing += 1
        else:
            df.loc[idxs[i], keys[i][1]] = vals
        done += 1
        if done % 2000 == 0:
            print(f'  [{done:,}/{len(tasks):,}] ({(time.time()-t0)/60:.1f}분)')
print(f'점추출 완료 (없거나 손상 {n_missing:,}건)')

for lag in range(1, LOOKBACK):
    df[f'vpd_tm{lag}'] = df[f'vpd_tm{lag}'].fillna(df['vpd_t0'])
    df[f'wind_tm{lag}'] = df[f'wind_tm{lag}'].fillna(df['wind_t0'])
for col in seq_cols:
    df[col] = df[col].fillna(0.0)

df['vpd'], df['wind'] = df['vpd_t0'], df['wind_t0']
df['doy'] = pd.to_datetime(df[['year', 'month', 'day']]).dt.dayofyear
df['doy_sin'] = np.sin(2 * np.pi * df['doy'] / 365)
df['doy_cos'] = np.cos(2 * np.pi * df['doy'] / 365)

df.drop(columns=['datetime']).to_parquet(OUT_PATH, index=False)
print(f'\n저장: {OUT_PATH}')
print(f'shape: {df.shape}  ({(time.time()-t0)/60:.1f}분)')
print('주요 피처 결측률:')
print(df[['dem', 'lc_conifer', 'pop_density', 'hum4d', 'prcp4d',
          'vpd_t0', 'wind_t0', 'ndvi', 'ndmi']].isnull().mean().round(4).to_string())
