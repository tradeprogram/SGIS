"""
사례 replay 생성 — 하루를 시간대별로 추론해 시간 슬라이더용 데이터셋을 만든다.

32번(단일 시각 추론)을 여러 시각에 반복하되, 재사용 가능한 래스터를 한 번만 읽는다.
  한 번만 읽는 것 : 지형 5, 토지피복 6, 인문환경 4, 산림비율, NDVI/NDMI, 일별 hum4d/prcp4d
  시각마다 바뀌는 것: vpd/wind 12랙 → 인접 시각끼리 11개가 겹치므로 전역 캐시로 처리

예: 06~18시(13개 시각)를 돌려도 실제로 읽는 시간별 래스터는 약 24시각 × 2변수 = 48개뿐이다.

출력
  replay_{DATE}_grid.parquet    시각 × 격자 (WUI 격자만, 용량 절감)
  replay_{DATE}_summary.csv     시각별 요약 + 실제 발화 사건 대조
  replay_{DATE}_top.csv         시각별 우선지역 Top-N
"""

import os, glob, re, time
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol
import pyproj

import _stage2_model as S2
import _exposure as S2E

NAS     = r'V:\data'
DERIVED = r'C:\for_sgis\data\grid_data\derived'
MASK    = NAS + r'\mask\common_mask_500m_5179.tif'

DATE       = os.environ.get('DATE', '2025-03-22')
HOURS      = [int(h) for h in os.environ.get('HOURS', '6,7,8,9,10,11,12,13,14,15,16,17,18').split(',')]
# 연도 → fold 자동 선택. 그 해를 학습에서 뺀 모델을 써야 누수가 없다.
#   2021→fold1, 2022→fold2, 2023→fold3, 2024→fold4, 2025→fold5
FOLD_YEAR  = int(DATE[:4])
FOLD_NO    = S2.fold_of(FOLD_YEAR)
FOREST_MIN = 0.3
POP_MIN    = 10.0
W_HAZ      = 0.5
TOP_N      = 10
HORIZONS   = [1, 2, 3]
# 정적 입력 순서 — _exposure 가 아니라 모델 입력 순서다. 바꾸면 기여도가 뒤섞인다.
STATIC_NAMES = ['P_lgbm', 'ndvi', 'ndmi', 'hum4d', 'prcp4d', 'doy_sin', 'doy_cos']

t0 = time.time()
YEAR = int(DATE[:4])
print(f'사례 replay: {DATE}  시각 {HOURS[0]}~{HOURS[-1]}시 ({len(HOURS)}개)')

with rasterio.open(MASK) as src:
    mask_arr, T = src.read(1), src.transform
valid_rows, valid_cols = np.where(mask_arr == 1)
n = len(valid_rows)
print(f'유효 픽셀 {n:,}개')

_cache = {}


def rd(path):
    """래스터 전체를 읽어 유효픽셀만 반환. 시간별 래스터는 여러 시각이 공유하므로 캐시한다."""
    if path not in _cache:
        if not os.path.exists(path):
            _cache[path] = None
        else:
            with rasterio.open(path) as s:
                arr = s.read(1).astype(np.float32)
                nd = s.nodata
            if nd is not None:
                arr[arr == nd] = np.nan
            _cache[path] = arr[valid_rows, valid_cols]
    return _cache[path]


# ── 1회만 읽는 피처 ──────────────────────────────────────────────────
print('\n[1회] 정적·연도별 피처 로드 중...')
feat = {}
for name, p in {
    'dem':     NAS + r'\DEM\500m_aligned\dem_500m_5179.tif',
    'slope':   NAS + r'\DEM\500m_aligned\slope_500m_5179.tif',
    'asp_cos': NAS + r'\DEM\500m_aligned\aspect_500m_cos_5179.tif',
    'asp_sin': NAS + r'\DEM\500m_aligned\aspect_500m_sin_5179.tif',
    'twi':     NAS + r'\DEM\500m_aligned\twi_500m_5179.tif',
}.items():
    feat[name] = rd(p)

forest = np.zeros(n, dtype=np.float32)
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

# 일별 (하루치이므로 1회)
ymd = DATE.replace('-', '')
ym  = ymd[:6]
for p, c in [(NAS + rf'\humidity_4day\{ym}\hm_4day_{ymd}.tif', 'hum4d'),
             (NAS + rf'\precip_4day_masked\{ym}\rn_4day_{ymd}.tif', 'prcp4d')]:
    v = rd(p)
    feat[c] = v if v is not None else np.full(n, np.nan, np.float32)

# NDVI/NDMI — 결측을 이전 합성본으로 순차 보완
ref_dt = pd.Timestamp(f'{DATE} {max(HOURS):02d}:00')
for kind, c in [('ndvi', 'ndvi'), ('ndmi', 'ndmi')]:
    files = sorted(glob.glob(NAS + rf'\mod09a1_{kind}\*\mod_{"ndvi" if kind=="ndvi" else "ndmi"}_*.tif'))
    dates = [pd.Timestamp(re.search(r'(\d{8})', os.path.basename(f)).group(1)) for f in files]
    before = sorted([d for d in dates if d + pd.Timedelta(days=8) <= ref_dt], reverse=True)
    out = np.full(n, np.nan, np.float32)
    for d in before[:5]:
        v = rd(files[dates.index(d)])
        if v is None:
            continue
        m = np.isnan(out)
        out[m] = v[m]
        if not np.isnan(out).any():
            break
    feat[c] = out
print(f'  완료 ({(time.time()-t0)/60:.1f}분)')

# ── 노출 ─────────────────────────────────────────────────────────────
# 노출항은 주간 보정 인구다. 산불은 낮에 나는데 상주인구는 야간 기준이라
# 그대로 쓰면 낮에 비는 아파트 밀집지가 위로 올라온다. 자세한 근거와 한계는
# _exposure.py 주석 참고.
base = S2E.build(valid_rows, valid_cols)
base['forest_ratio'] = forest
base['is_wui'] = (base['forest_ratio'] >= FOREST_MIN) & (base['pop_total'] >= POP_MIN)
base['expo_rank'] = base['pop_expo'].rank(pct=True) * 100
print(f'WUI 격자 {int(base["is_wui"].sum()):,}개')

# ── 모델 ─────────────────────────────────────────────────────────────
lgbm, _scaler, infer, _desc = S2.load(FOLD_YEAR)

FEATURE_COLS = [
    'dem', 'slope', 'asp_cos', 'asp_sin', 'twi',
    'lc_urban', 'lc_deciduous', 'lc_conifer', 'lc_mixed_forest', 'lc_grass', 'lc_water',
    'pop_density', 'cropland', 'settlement_dist', 'road_density',
    'hum4d', 'prcp4d', 'vpd', 'wind', 'ndvi', 'ndmi', 'doy_sin', 'doy_cos',
]

# ── 실제 발화 사건 (대조용) ──────────────────────────────────────────
g = pd.read_csv(NAS + r'\wildfire_reference\fire_events_geocoded.csv', encoding='utf-8-sig')
g['dt'] = pd.to_datetime(g['datetime'])
rec = pd.read_csv(os.path.join(DERIVED, 'fire_events_geocode_recovered.csv'), encoding='utf-8-sig')
day = g[(g['dt'] >= f'{DATE} 00:00') & (g['dt'] < f'{DATE} 23:59')].copy()
tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)
ev = []
for r in day.itertuples():
    if pd.notna(r.lon):
        x, y = tr.transform(r.lon, r.lat)
    else:
        m = rec[rec['fire_id'] == r.fire_id]
        if len(m) == 0 or pd.isna(m.iloc[0]['x_5179']):
            continue
        x, y = m.iloc[0]['x_5179'], m.iloc[0]['y_5179']
    pr, pc = rowcol(T, x, y)
    ev.append({'fire_id': r.fire_id, 'dt': r.dt, 'ignite_h': pd.Timestamp(r.dt).floor('h'),
               'prow': pr, 'pcol': pc, 'damagearea': r.damagearea,
               'loc': f'{r.locsi} {r.locgungu} {r.locmenu}'})
# 발화 0건인 날도 대상이므로 빈 경우에도 컬럼을 갖춘 프레임을 만든다
ev = pd.DataFrame(ev, columns=['fire_id', 'dt', 'ignite_h', 'prow', 'pcol',
                               'damagearea', 'loc'])
print(f'{DATE} 실제 발화 {len(ev)}건')

# ── 시각별 루프 ──────────────────────────────────────────────────────
grid_out, summary, tops, full_grid = [], [], [], []
for hh in HOURS:
    TT = pd.Timestamp(f'{DATE} {hh:02d}:00')
    prev = TT - pd.Timedelta(hours=1)
    a, b, c = f'{prev.year}{prev.month:02d}', f'{prev.year}{prev.month:02d}{prev.day:02d}', f'{prev.hour:02d}00'
    v = rd(NAS + rf'\vpd_moedel2\{a}\vpd_{b}_{c}.tif')
    w = rd(NAS + rf'\wind_model2\{a}\wind_speed_{b}_{c}.tif')
    feat['vpd']  = v if v is not None else np.full(n, np.nan, np.float32)
    feat['wind'] = w if w is not None else np.full(n, np.nan, np.float32)
    doy = TT.dayofyear
    feat['doy_sin'] = np.full(n, np.sin(2 * np.pi * doy / 365), np.float32)
    feat['doy_cos'] = np.full(n, np.cos(2 * np.pi * doy / 365), np.float32)

    X = np.column_stack([feat[cc] for cc in FEATURE_COLS]).astype(np.float32)
    ok = ~np.isnan(X).any(axis=1)
    P = np.full(n, np.nan, np.float32)
    P[ok] = lgbm.predict_proba(X[ok])[:, 1]

    sv = np.full((n, 12), np.nan, np.float32)
    sw = np.full((n, 12), np.nan, np.float32)
    sv[:, 11], sw[:, 11] = feat['vpd'], feat['wind']
    for lag in range(1, 12):
        rdt = TT - pd.Timedelta(hours=lag + 1)
        a2, b2, c2 = f'{rdt.year}{rdt.month:02d}', f'{rdt.year}{rdt.month:02d}{rdt.day:02d}', f'{rdt.hour:02d}00'
        i = 11 - lag
        vv = rd(NAS + rf'\vpd_moedel2\{a2}\vpd_{b2}_{c2}.tif')
        ww = rd(NAS + rf'\wind_model2\{a2}\wind_speed_{b2}_{c2}.tif')
        if vv is not None: sv[:, i] = vv
        if ww is not None: sw[:, i] = ww
    for lag in range(11):
        m = np.isnan(sv[:, lag]); sv[m, lag] = sv[m, 11]
        m = np.isnan(sw[:, lag]); sw[m, lag] = sw[m, 11]
    sv, sw = np.nan_to_num(sv), np.nan_to_num(sw)

    seq = np.stack([sv, sw], axis=-1)
    st = np.column_stack([np.nan_to_num(P), np.nan_to_num(feat['ndvi']), np.nan_to_num(feat['ndmi']),
                          np.nan_to_num(feat['hum4d']), np.nan_to_num(feat['prcp4d']),
                          feat['doy_sin'], feat['doy_cos']]).astype(np.float32)
    probs = infer(seq, st)
    probs[~ok] = np.nan

    d = base.copy()
    d['T'] = TT
    # 우측 패널에 띄울 실제 모델 입력값 (참고지표가 아니라 진짜 입력이라는 게 요점)
    for _c2, _v2 in [('vpd', feat['vpd']), ('wind', feat['wind']),
                     ('hum4d', feat['hum4d']), ('prcp4d', feat['prcp4d']),
                     ('ndmi', feat['ndmi'])]:
        d[_c2] = _v2
    for k, H in enumerate(HORIZONS):
        d[f'y_prob_t{H}'] = probs[:, k]
        d[f'haz_rank_t{H}'] = pd.Series(probs[:, k]).rank(pct=True, na_option='keep').values * 100
        d[f'haz_top_t{H}'] = 100 - d[f'haz_rank_t{H}']
    d['score_t1'] = W_HAZ * d['haz_rank_t1'] + (1 - W_HAZ) * d['expo_rank']
    d.loc[~d['is_wui'], 'score_t1'] = np.nan

    top = d.nlargest(TOP_N, 'score_t1')

    # ── 왜 이 격자가 위험한가 (occlusion 기여도) ──────────────────────
    # SHAP 대신 occlusion 을 쓴다. 이 모델의 입력은 12시간 시계열이라
    # "최근 몇 시간 중 어느 시점이 결정적이었나"가 진화 지휘에 직접 쓰이는데,
    # 시점을 하나씩 그 격자 자신의 12시간 평균으로 덮어 출력 변화를 보면
    # 그 답이 그대로 나온다. 추가 라이브러리도, 배경분포 가정도 필요 없다.
    #   기여도 > 0  = 그 시점(또는 그 피처)이 위험도를 끌어올렸다
    ti = top.index.to_numpy()
    if len(ti):
        sq_t, st_t = seq[ti], st[ti]                     # (K,12,2), (K,7)
        # 정적 피처 기준선은 그 시각 WUI 격자의 중앙값. 전국 중앙값을 쓰면
        # "산림 인접지치고 어떤가"가 아니라 "전국 평균 대비"가 되어 해석이 흐려진다.
        st_base = np.nanmedian(st[d['is_wui'].values], axis=0)
        K = len(ti)
        var_sq, var_st = [sq_t], [st_t]
        for k in range(12):                              # 시간축 12
            v = sq_t.copy()
            v[:, k, :] = sq_t.mean(axis=1)
            var_sq.append(v); var_st.append(st_t)
        for j in range(st_t.shape[1]):                   # 정적 7
            v = st_t.copy()
            v[:, j] = st_base[j]
            var_sq.append(sq_t); var_st.append(v)
        pv = infer(np.concatenate(var_sq), np.concatenate(var_st))[:, 0].reshape(-1, K)
        contrib = pv[0] - pv[1:]                         # (19, K)
        for c in range(12):
            # 컬럼명에 '-' 를 쓰면 itertuples 가 조용히 위치 이름(_12 등)으로
            # 바꿔 버려 이름 접근이 깨진다. h00 = t-11h … h11 = t0.
            top[f'occ_h{c:02d}'] = contrib[c]
        for j, nm2 in enumerate(STATIC_NAMES):
            top[f'occ_{nm2}'] = contrib[12 + j]

    tops.append(top.assign(rank=range(1, len(top) + 1)))

    # 실제 발화 대조: T+1~T+3h 에 발화한 사건의 위험 백분위
    hit = []
    for H in HORIZONS:
        tgt = TT + pd.Timedelta(hours=H)
        for e in ev[ev['ignite_h'] == tgt].itertuples():
            row = d[(d['prow'] == e.prow) & (d['pcol'] == e.pcol)]
            if len(row):
                hit.append({'loc': e.loc, 'ha': e.damagearea, 'H': H,
                            'top_pct': float(row[f'haz_top_t{H}'].iloc[0])})

    summary.append({
        'T': TT,
        'top1pct_인구': float(d.loc[d['haz_top_t1'] <= 1, 'pop_total'].sum()),
        'top5pct_인구': float(d.loc[d['haz_top_t1'] <= 5, 'pop_total'].sum()),
        # 상주인구는 그대로 두고 주간·고령·노후주택을 나란히 남긴다.
        # 기존 화면과 비교가 되어야 보정 효과를 설명할 수 있다.
        'top1pct_주간인구': float(d.loc[d['haz_top_t1'] <= 1, 'pop_day'].sum()),
        'top5pct_주간인구': float(d.loc[d['haz_top_t1'] <= 5, 'pop_day'].sum()),
        'top1pct_고령': float(d.loc[d['haz_top_t1'] <= 1, 'pop_old'].sum()),
        'top5pct_고령': float(d.loc[d['haz_top_t1'] <= 5, 'pop_old'].sum()),
        'top5pct_노후주택': float(d.loc[d['haz_top_t1'] <= 5, 'old_house'].sum()),
        'wui_top5pct_격자': int(((d['haz_top_t1'] <= 5) & d['is_wui']).sum()),
        'top10_인구': float(top['pop_total'].sum()),
        'top10_주간인구': float(top['pop_day'].sum()),
        'top10_고령': float(top['pop_old'].sum()),
        'top10_평균위험상위%': round(float(top['haz_top_t1'].mean()), 3),
        # 공간 백분위만 보면 조용한 날이 더 위험해 보인다.
        # 시간축 비교(오늘이 5년 중 얼마나 위험한 날인가)를 위해 절대값도 남긴다.
        'max_prob': float(np.nanmax(probs[:, 0])),
        'mean_prob': float(np.nanmean(probs[:, 0])),
        '발화건수': len(hit),
        '발화_위험상위%': '; '.join(f"{h['loc']}({h['ha']}ha,t+{h['H']}h)={h['top_pct']:.1f}%" for h in hit),
    })
    grid_out.append(d.loc[d['is_wui'], ['T', 'prow', 'pcol', 'y_prob_t1', 'y_prob_t2', 'y_prob_t3',
                                        'haz_top_t1', 'score_t1', 'pop_total',
                                        'pop_day', 'pop_old', 'old_house', 'avg_age',
                                        'vpd', 'wind', 'hum4d', 'prcp4d', 'ndmi']])
    full_grid.append(np.stack([d['haz_top_t1'].values, d['haz_top_t2'].values,
                               d['haz_top_t3'].values]).astype(np.float32))
    print(f'  {TT:%H:%M}  상위1% 인구 {summary[-1]["top1pct_인구"]:>10,.0f}명  '
          f'발화 {len(hit)}건  ({(time.time()-t0)/60:.1f}분)')

pd.concat(grid_out, ignore_index=True).to_parquet(
    os.path.join(DERIVED, f'replay_{ymd}_grid.parquet'), index=False)

# 전국 배경 PNG 렌더링용 — 유효픽셀 전체의 위험 백분위 (시각 × horizon × 픽셀)
np.savez_compressed(
    os.path.join(DERIVED, f'replay_{ymd}_full.npz'),
    prow=valid_rows.astype(np.int16), pcol=valid_cols.astype(np.int16),
    hours=np.array(HOURS, dtype=np.int16),
    haz_top=np.stack(full_grid).astype(np.float32),   # (시각, 3, 픽셀) — 0 = 전국 1위
)
sdf = pd.DataFrame(summary)
sdf.to_csv(os.path.join(DERIVED, f'replay_{ymd}_summary.csv'), index=False, encoding='utf-8-sig')
pd.concat(tops, ignore_index=True).to_csv(
    os.path.join(DERIVED, f'replay_{ymd}_top.csv'), index=False, encoding='utf-8-sig')

print(f'\n{"="*100}')
print(sdf[['T', 'top1pct_인구', 'top5pct_인구', 'wui_top5pct_격자',
           'top10_인구', 'top10_평균위험상위%', '발화건수']].to_string(index=False))
print(f'\n실제 발화 대조:')
# itertuples 의 위치 기반 접근(r._8)은 summary 컬럼이 하나만 늘어도 어긋난다.
# 실제로 max_prob·mean_prob 를 추가했을 때 깨졌다. 이름으로 접근한다.
for _, r in sdf.iterrows():
    if r['발화_위험상위%']:
        print(f'  {r["T"]:%H:%M} → {r["발화_위험상위%"]}')
print(f'\n저장 완료 ({(time.time()-t0)/60:.1f}분)  캐시 래스터 {len(_cache)}개')
