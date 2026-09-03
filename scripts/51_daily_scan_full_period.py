"""
Tier 1 — 2021~2025 산불시즌 전 기간(747일) 일별 스캔.

왜 하는가
  1) "대형산불 날만 골라 보여준다"는 인상을 없앤다. 발화가 없던 305일도 그대로 보여준다.
  2) 실제 발화 1,700여 건 전수에 대해 "발화 1~3시간 전 시점에 그 격자가 전국 상위 몇 %였는가"를
     계산한다. 하루치 22건으로는 경향 관찰밖에 못 했던 것을 성과지표로 바꾼다.

지도 이미지는 만들지 않는다. 하루당 집계값 한 줄 + 그날 발화의 순위만 남긴다(747행, 수백 KB).
시간별 지도는 52번이 선별한 날에 대해서만 만든다.

누수 방지
  대상 연도를 학습에서 제외한 fold 모델을 쓴다 (2022년 → fold2). LightGBM Stage1도 동일.

기본 스캔 시각은 SCAN_HOURS(기본 11·14시). 산불이 오후에 몰리므로 이 두 시각이
t+1~t+3h로 12~17시를 덮는다. 하루 2시각이면 747일 × 2 = 1,494회 추론.

출력
  daily_scan_{YYYY}.parquet     연도별 일별 집계 (중간 저장, 재시작 가능)
  daily_scan_all.csv            747일 통합
  ignition_ranks.csv            발화 사건별 예측 순위 (성과지표 원자료)
"""

import os, glob, re, time
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol
import pyproj
from PIL import Image
from matplotlib.colors import LinearSegmentedColormap
from rasterio.warp import calculate_default_transform, reproject, Resampling

import _stage2_model as S2

NAS     = r'V:\data'
DERIVED = r'C:\for_sgis\data\grid_data\derived'
MASK    = NAS + r'\mask\common_mask_500m_5179.tif'
OUT_DIR = os.path.join(DERIVED, 'daily_scan')

YEARS_ALL     = [2021, 2022, 2023, 2024, 2025]
MONTHS        = [2, 3, 4, 5, 6]
DATA_CAP_2025 = pd.Timestamp('2025-06-26 06:00:00')
SCAN_HOURS    = [int(h) for h in os.environ.get('SCAN_HOURS', '11,14').split(',')]
ONLY_YEAR     = os.environ.get('ONLY_YEAR')          # 연도별 분할 실행용
FOREST_MIN, POP_MIN, W_HAZ = 0.3, 10.0, 0.5
HORIZONS      = [1, 2, 3]

os.makedirs(OUT_DIR, exist_ok=True)
t_all = time.time()


with rasterio.open(MASK) as src:
    mask_arr, T = src.read(1), src.transform
    shape = (src.height, src.width)
valid_rows, valid_cols = np.where(mask_arr == 1)
n = len(valid_rows)
print(f'유효 픽셀 {n:,}개 | 스캔 시각 {SCAN_HOURS}')

# ── 전 기간 일별 지도 PNG (DUMP_PNG=1) ───────────────────────────────
# 741일 전부에 대해 지도를 두면 "사례 몇 건"이 아니라 "매일 돌아간 시스템"이 된다.
# 스캔은 이미 전 격자 추론을 하고 있으므로, 여기서 PNG 한 장을 더 굽는 비용은
# 사실상 0이다. 별도 패스로 돌리면 741회 추론을 다시 해야 해서 3.8시간이 든다.
DUMP_PNG   = os.environ.get('DUMP_PNG') == '1'
PNG_DIR    = os.path.join(r'C:', os.sep, 'for_sgis', 'web', 'public', 'data', 'daily')
PNG_DOWN   = int(os.environ.get('PNG_DOWNSCALE', '4'))   # 사례일(2)보다 거칠게
PNG_TOP    = float(os.environ.get('PNG_TOP_PCT', '20'))  # 상위 20%까지 착색

if DUMP_PNG:
    os.makedirs(PNG_DIR, exist_ok=True)
    with rasterio.open(MASK) as _s:
        _bounds, _crs = _s.bounds, _s.crs
    _H, _W = shape
    _dt0, _dw, _dh = calculate_default_transform(_crs, 'EPSG:3857', _W, _H, *_bounds)
    _dw, _dh = _dw // PNG_DOWN, _dh // PNG_DOWN
    _dtr = rasterio.Affine(_dt0.a * PNG_DOWN, 0, _dt0.c, 0, _dt0.e * PNG_DOWN, _dt0.f)
    _l, _t = _dtr.c, _dtr.f
    _r, _b = _l + _dtr.a * _dw, _t + _dtr.e * _dh
    _tf = pyproj.Transformer.from_crs('EPSG:3857', 'EPSG:4326', always_xy=True)
    (_lx, _ty), (_rx, _by) = _tf.transform(_l, _t), _tf.transform(_r, _b)
    PNG_CORNERS = [[_lx, _ty], [_rx, _ty], [_rx, _by], [_lx, _by]]
    _cmap = LinearSegmentedColormap.from_list(
        'fire', ['#22d3ee', '#a3e635', '#facc15', '#fb923c', '#ef4444'])
    print(f'PNG 덤프 켜짐 → {PNG_DIR}  ({_dw}x{_dh}, downscale {PNG_DOWN})')

def dump_png(haz_t1, day, hh):
    src_arr = np.full(shape, np.nan, dtype=np.float32)
    src_arr[valid_rows, valid_cols] = haz_t1
    dst = np.full((_dh, _dw), np.nan, dtype=np.float32)
    reproject(src_arr, dst, src_transform=T, src_crs=_crs,
              dst_transform=_dtr, dst_crs='EPSG:3857',
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
    t = np.clip(1.0 - dst / PNG_TOP, 0.0, 1.0)
    t[np.isnan(dst)] = 0.0
    rgba = (_cmap(t) * 255).astype(np.uint8)
    a = np.where(np.isnan(dst), 0, (30 + 200 * t)).astype(np.uint8)
    a[t <= 0.001] = 0
    rgba[:, :, 3] = a
    Image.fromarray(rgba, 'RGBA').save(
        os.path.join(PNG_DIR, f'{day:%Y%m%d}_{hh:02d}.png'), optimize=True)

# ── 노출·행정동 (연도 무관, 1회) ─────────────────────────────────────
exp = pd.read_parquet(os.path.join(DERIVED, 'mask_exposure_500m.parquet'))
adm = pd.read_parquet(os.path.join(DERIVED, 'cell_admin.parquet'))
base0 = pd.DataFrame({'prow': valid_rows.astype(np.int32), 'pcol': valid_cols.astype(np.int32)})
base0 = base0.merge(exp[['prow', 'pcol', 'pop_total']], on=['prow', 'pcol'], how='left')
base0 = base0.merge(adm[['prow', 'pcol', 'adm_cd', 'adm_nm']].astype({'prow': 'int32', 'pcol': 'int32'}),
                    on=['prow', 'pcol'], how='left')
base0['expo_rank'] = base0['pop_total'].rank(pct=True) * 100
print(f'노출·행정동 결합 완료 (행정동 {base0["adm_cd"].nunique():,}개)')

# ── 실제 발화 (원본 + SGIS 복구 좌표) ────────────────────────────────
geo = pd.read_csv(NAS + r'\wildfire_reference\fire_events_geocoded.csv', encoding='utf-8-sig')
geo['dt'] = pd.to_datetime(geo['datetime'])
rec = pd.read_csv(os.path.join(DERIVED, 'fire_events_geocode_recovered.csv'), encoding='utf-8-sig')
rec = rec[rec['recover_level'].isin(['ri', 'dong'])]
tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)

ev = []
for r in geo[(geo['dt'].dt.year.between(2021, 2025)) & (geo['dt'].dt.month.isin(MONTHS))].itertuples():
    if pd.notna(r.lon):
        x, y = tr.transform(r.lon, r.lat)
    else:
        m = rec[rec['fire_id'] == r.fire_id]
        if len(m) == 0 or pd.isna(m.iloc[0]['x_5179']):
            continue
        x, y = float(m.iloc[0]['x_5179']), float(m.iloc[0]['y_5179'])
    pr, pc = rowcol(T, x, y)
    if not (0 <= pr < shape[0] and 0 <= pc < shape[1]) or mask_arr[pr, pc] != 1:
        continue
    ev.append({'fire_id': int(r.fire_id), 'ignite_h': pd.Timestamp(r.dt).floor('h'),
               'prow': int(pr), 'pcol': int(pc), 'damagearea': float(r.damagearea or 0),
               'loc': f'{r.locsi} {r.locgungu} {r.locmenu}'})
ev = pd.DataFrame(ev)
ev['date'] = ev['ignite_h'].dt.date
print(f'실제 발화 사건 {len(ev):,}건')

FEATURE_COLS = [
    'dem', 'slope', 'asp_cos', 'asp_sin', 'twi',
    'lc_urban', 'lc_deciduous', 'lc_conifer', 'lc_mixed_forest', 'lc_grass', 'lc_water',
    'pop_density', 'cropland', 'settlement_dist', 'road_density',
    'hum4d', 'prcp4d', 'vpd', 'wind', 'ndvi', 'ndmi', 'doy_sin', 'doy_cos',
]

years = [int(ONLY_YEAR)] if ONLY_YEAR else YEARS_ALL

# 다른 스캔 시각으로 추가 실행할 때 기존 산출물을 덮지 않게 한다.
# 최종 통합은 daily_scan_*.parquet 을 glob 하므로 접미사가 붙어도 자동으로 합쳐진다.
SUFFIX = os.environ.get('OUT_SUFFIX', '')

for YEAR in years:
    out_path = os.path.join(OUT_DIR, f'daily_scan_{YEAR}{SUFFIX}.parquet')
    rank_path = os.path.join(OUT_DIR, f'ignition_ranks_{YEAR}{SUFFIX}.parquet')
    if os.path.exists(out_path) and not os.environ.get('FORCE'):
        print(f'\n{YEAR}: 이미 있음 → 건너뜀 ({out_path})')
        continue

    fno = S2.fold_of(YEAR)
    print(f"\n{'='*70}\n{YEAR}년 스캔 (그 해를 학습에서 제외한 fold {fno})\n{'='*70}")
    lgbm, _scaler, infer, _desc = S2.load(YEAR)

    # 연도 고정 피처는 해마다 1회만 읽는다
    _c = {}

    def rd(path):
        if path not in _c:
            if not os.path.exists(path):
                _c[path] = None
            else:
                with rasterio.open(path) as s:
                    a = s.read(1).astype(np.float32); nd = s.nodata
                if nd is not None:
                    a[a == nd] = np.nan
                _c[path] = a[valid_rows, valid_cols]
        return _c[path]

    feat = {}
    for nm, p in {
        'dem': NAS + r'\DEM\500m_aligned\dem_500m_5179.tif',
        'slope': NAS + r'\DEM\500m_aligned\slope_500m_5179.tif',
        'asp_cos': NAS + r'\DEM\500m_aligned\aspect_500m_cos_5179.tif',
        'asp_sin': NAS + r'\DEM\500m_aligned\aspect_500m_sin_5179.tif',
        'twi': NAS + r'\DEM\500m_aligned\twi_500m_5179.tif',
    }.items():
        feat[nm] = rd(p)

    forest = np.zeros(n, np.float32)
    for lc in ['urban', 'deciduous', 'conifer', 'mixed_forest', 'grass', 'water']:
        v = rd(NAS + rf'\landcover_raster\landcover_{lc}_ratio_{YEAR}.tif')
        feat[f'lc_{lc}'] = v if v is not None else np.full(n, np.nan, np.float32)
        if lc in ('conifer', 'deciduous', 'mixed_forest'):
            forest += np.nan_to_num(feat[f'lc_{lc}'], nan=0.0)

    dens_year = YEAR if YEAR <= 2024 else 2024
    road_year = 2021 if YEAR == 2021 else (2025 if YEAR == 2025 else 2022)
    for p, c in [
        (NAS + rf'\people_density\output\04density_aligned\people_density_{dens_year}_04_epsg5179_500m.tif', 'pop_density'),
        (NAS + rf'\cropland\cropland_ratio_{YEAR}_500m.tif', 'cropland'),
        (NAS + rf'\settlement\distance_to_settlement_{YEAR}_500m.tif', 'settlement_dist'),
        (NAS + rf'\road_distance\road_length_density_aligned\road_length_density_{road_year}_500m.tif', 'road_density'),
    ]:
        v = rd(p)
        feat[c] = v if v is not None else np.full(n, np.nan, np.float32)

    is_wui = (forest >= FOREST_MIN) & (base0['pop_total'].values >= POP_MIN)
    pop = np.nan_to_num(base0['pop_total'].values, nan=0.0)
    expo_rank = np.nan_to_num(base0['expo_rank'].values, nan=0.0)

    ndvi_f = sorted(glob.glob(NAS + r'\mod09a1_ndvi\*\mod_ndvi_*.tif'))
    ndmi_f = sorted(glob.glob(NAS + r'\mod09a1_ndmi\*\mod_ndmi_*.tif'))
    ndvi_d = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndvi_f]
    ndmi_d = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in ndmi_f]

    dates = pd.date_range(f'{YEAR}-02-01', f'{YEAR}-06-30', freq='D')
    dates = [d for d in dates if d.month in MONTHS and not (YEAR == 2025 and d > DATA_CAP_2025)]
    _lim = int(os.environ.get('LIMIT_DAYS', '0'))       # 스모크 테스트용
    if _lim > 0:
        dates = dates[:_lim]
        print(f'[LIMIT_DAYS={_lim}] {len(dates)}일만 처리')

    rows, rank_rows = [], []
    for di, day in enumerate(dates):
        ymd, ym = day.strftime('%Y%m%d'), day.strftime('%Y%m')
        for p, c in [(NAS + rf'\humidity_4day\{ym}\hm_4day_{ymd}.tif', 'hum4d'),
                     (NAS + rf'\precip_4day_masked\{ym}\rn_4day_{ymd}.tif', 'prcp4d')]:
            v = rd(p)
            feat[c] = v if v is not None else np.full(n, np.nan, np.float32)

        for files, dts, c in [(ndvi_f, ndvi_d, 'ndvi'), (ndmi_f, ndmi_d, 'ndmi')]:
            before = sorted([d for d in dts if d + pd.Timedelta(days=8) <= day], reverse=True)
            o = np.full(n, np.nan, np.float32)
            for d in before[:5]:
                v = rd(files[dts.index(d)])
                if v is None:
                    continue
                msk = np.isnan(o); o[msk] = v[msk]
                if not np.isnan(o).any():
                    break
            feat[c] = o

        doy = day.dayofyear
        feat['doy_sin'] = np.full(n, np.sin(2 * np.pi * doy / 365), np.float32)
        feat['doy_cos'] = np.full(n, np.cos(2 * np.pi * doy / 365), np.float32)

        for hh in SCAN_HOURS:
            TT = pd.Timestamp(day.year, day.month, day.day, hh)
            prev = TT - pd.Timedelta(hours=1)
            a, b, c2 = prev.strftime('%Y%m'), prev.strftime('%Y%m%d'), f'{prev.hour:02d}00'
            v = rd(NAS + rf'\vpd_moedel2\{a}\vpd_{b}_{c2}.tif')
            w = rd(NAS + rf'\wind_model2\{a}\wind_speed_{b}_{c2}.tif')
            if v is None or w is None:
                continue
            feat['vpd'], feat['wind'] = v, w

            X = np.column_stack([feat[cc] for cc in FEATURE_COLS]).astype(np.float32)
            ok = ~np.isnan(X).any(axis=1)
            if ok.sum() == 0:
                continue
            P = np.full(n, np.nan, np.float32)
            P[ok] = lgbm.predict_proba(X[ok])[:, 1]

            sv = np.full((n, 12), np.nan, np.float32)
            sw = np.full((n, 12), np.nan, np.float32)
            sv[:, 11], sw[:, 11] = v, w
            for lag in range(1, 12):
                rdt = TT - pd.Timedelta(hours=lag + 1)
                a2, b2, c3 = rdt.strftime('%Y%m'), rdt.strftime('%Y%m%d'), f'{rdt.hour:02d}00'
                vv = rd(NAS + rf'\vpd_moedel2\{a2}\vpd_{b2}_{c3}.tif')
                ww = rd(NAS + rf'\wind_model2\{a2}\wind_speed_{b2}_{c3}.tif')
                if vv is not None: sv[:, 11 - lag] = vv
                if ww is not None: sw[:, 11 - lag] = ww
            for lag in range(11):
                msk = np.isnan(sv[:, lag]); sv[msk, lag] = sv[msk, 11]
                msk = np.isnan(sw[:, lag]); sw[msk, lag] = sw[msk, 11]
            sv, sw = np.nan_to_num(sv), np.nan_to_num(sw)

            st = np.column_stack([np.nan_to_num(P), np.nan_to_num(feat['ndvi']),
                                  np.nan_to_num(feat['ndmi']), np.nan_to_num(feat['hum4d']),
                                  np.nan_to_num(feat['prcp4d']), feat['doy_sin'],
                                  feat['doy_cos']]).astype(np.float32)
            probs = infer(np.stack([sv, sw], -1), st, chunk=16384)
            probs[~ok] = np.nan

            # 위험 백분위 (0 = 전국 1위)
            haz_top = np.full((n, 3), np.nan, np.float32)
            for k in range(3):
                s_ = pd.Series(probs[:, k])
                haz_top[:, k] = (100 - s_.rank(pct=True, na_option='keep').values * 100).astype(np.float32)

            score = W_HAZ * (100 - haz_top[:, 0]) + (1 - W_HAZ) * expo_rank
            score[~is_wui] = np.nan

            t1 = haz_top[:, 0]
            if DUMP_PNG:
                dump_png(t1, day, hh)
            rows.append({
                'date': day.date(), 'hour': hh,
                'top1_pop': float(pop[t1 <= 1].sum()),
                'top5_pop': float(pop[t1 <= 5].sum()),
                'wui_top5_cells': int(((t1 <= 5) & is_wui).sum()),
                'max_prob': float(np.nanmax(probs[:, 0])),
                'mean_prob': float(np.nanmean(probs[:, 0])),
                'top10_pop': float(pop[np.argsort(-np.nan_to_num(score, nan=-1))[:10]].sum()),
            })

            # 이 시각의 t+1~t+3h에 실제로 발화한 사건의 순위
            for H in HORIZONS:
                tgt = TT + pd.Timedelta(hours=H)
                for e in ev[ev['ignite_h'] == tgt].itertuples():
                    j = np.flatnonzero((valid_rows == e.prow) & (valid_cols == e.pcol))
                    if len(j) == 0:
                        continue
                    rank_rows.append({
                        'fire_id': e.fire_id, 'ignite_h': e.ignite_h, 'scan_hour': hh,
                        'horizon': H, 'loc': e.loc, 'damagearea': e.damagearea,
                        'haz_top_pct': float(haz_top[j[0], H - 1]),
                        'is_wui': bool(is_wui[j[0]]), 'pop': float(pop[j[0]]),
                    })

        if (di + 1) % 20 == 0:
            print(f'  [{di+1:>3}/{len(dates)}] {day.date()}  ({(time.time()-t_all)/60:.1f}분)')
        if len(_c) > 120:                      # 시간별 래스터 캐시 정리
            for k in list(_c.keys())[:-60]:
                del _c[k]

    pd.DataFrame(rows).to_parquet(out_path, index=False)
    pd.DataFrame(rank_rows).to_parquet(rank_path, index=False)
    print(f'{YEAR} 완료: {len(rows)}개 시각-일, 발화순위 {len(rank_rows)}건 → {out_path}')

# ── 통합 ─────────────────────────────────────────────────────────────
# 어떤 접미사를 한 덩어리로 합칠지 명시한다. glob 로 전부 긁으면 모델이 다른
# A/B 산출물(_cnn20 등)까지 섞여 들어가 통합 CSV 가 조용히 오염된다.
#   OUT_SUFFIX 없음 → 기본 스캔(접미사 없음) + 추가 시각(_h08/_h10) 을 합친다
#   OUT_SUFFIX 있음 → 그 접미사끼리만 합치고, 결과도 접미사를 달고 나간다
MERGE_SUFFIXES = ([SUFFIX] if SUFFIX
                  else os.environ.get('MERGE_SUFFIXES', ',_h08,_h10').split(','))


def _in_set(path: str, kind: str) -> bool:
    stem = os.path.splitext(os.path.basename(path))[0]
    return any(stem == f'{kind}_{y}{s}' for y in YEARS_ALL for s in MERGE_SUFFIXES)


sc = sorted(f for f in glob.glob(os.path.join(OUT_DIR, 'daily_scan_*.parquet'))
            if _in_set(f, 'daily_scan'))
rk = sorted(f for f in glob.glob(os.path.join(OUT_DIR, 'ignition_ranks_*.parquet'))
            if _in_set(f, 'ignition_ranks'))
have = {y for y in YEARS_ALL if any(f'daily_scan_{y}' in os.path.basename(f) for f in sc)}
if have == set(YEARS_ALL):
    print(f'\n통합 대상 {len(sc)}개 파일 (접미사 {MERGE_SUFFIXES})')
    d = pd.concat([pd.read_parquet(f) for f in sc], ignore_index=True)
    d.to_csv(os.path.join(DERIVED, f'daily_scan_all{SUFFIX}.csv'), index=False, encoding='utf-8-sig')
    r = pd.concat([pd.read_parquet(f) for f in rk], ignore_index=True)
    r.to_csv(os.path.join(DERIVED, f'ignition_ranks{SUFFIX}.csv'), index=False, encoding='utf-8-sig')
    print(f'\n통합: 일별 {len(d):,}행 / 발화순위 {len(r):,}행')
    best = r.groupby('fire_id')['haz_top_pct'].min()
    print(f'\n발화 사건 {len(best):,}건의 최선 순위 분포:')
    for th in [1, 5, 10, 20]:
        print(f'  전국 상위 {th:>2}% 이내: {100*(best <= th).mean():>5.1f}%')
    print(f'  중앙값 {best.median():.1f}%')

print(f'\n총 소요 {(time.time()-t_all)/60:.1f}분')
