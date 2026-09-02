"""
Tier 2 다일 자산 빌더 — 52번이 고른 25일의 시간별 지도를 웹 자산으로 만든다.

용량 설계
  일자별 GeoJSON을 그대로 두면 4MB x 25일 = 100MB가 넘는다.
  격자는 규칙적인 500m 사각형이므로 지오메트리를 전 일자 공용 1개 파일에 압축해 담고,
  일자별로는 값 배열만 둔다.

    cells.json   전 일자 합집합 셀 — 경계 bbox(4326, 1e5 정수) + 지명사전 + SGIS 노출
    d/{YMD}/     일자별 PNG 13장 + values.json + priority.json + fires.json + meta.json

  대략 PNG 31MB + cells 3MB + 값 4MB = 약 38MB.

절차
  1) 각 일자에 대해 35번(사례 replay)을 실행한다. 이미 산출물이 있으면 건너뛴다.
  2) 전 일자의 상위 VEC_PCT 셀을 합쳐 공용 인덱스를 만든다.
  3) 일자별 PNG/값/우선지역/발화점을 쓴다.

연도별 fold 모델이 모두 필요하다 (24번에서 5개 저장 후 실행).
"""

import os, json, subprocess, sys, time
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image
import pyproj

DERIVED = r'C:\for_sgis\data\grid_data\derived'
MASK    = r'V:\data\mask\common_mask_500m_5179.tif'
WEB     = r'C:\for_sgis\web\public\data'
SCRIPTS = r'C:\for_sgis\scripts'

HOURS   = '6,7,8,9,10,11,12,13,14,15,16,17,18'
TOP_PCT = 20.0
VEC_PCT = 3.0
TOP_N   = 10
PNG_DOWNSCALE = 2
ONLY    = os.environ.get('ONLY_DATES')      # 쉼표 구분, 부분 실행용

LEVELS = [
    {'key': 'high',   'label': '매우 높음', 'max_pct': 1,  'color': '#ef4444'},
    {'key': 'mod',    'label': '높음',     'max_pct': 5,  'color': '#fb923c'},
    {'key': 'watch',  'label': '주의',     'max_pct': 10, 'color': '#facc15'},
    {'key': 'normal', 'label': '보통',     'max_pct': 20, 'color': '#a3e635'},
]
SIGNALS = [
    {'key': 'vpd',    'label': '증기압차 VPD',  'unit': 'hPa', 'dir': 'up'},
    {'key': 'wind',   'label': '풍속',         'unit': 'm/s', 'dir': 'up'},
    {'key': 'hum4d',  'label': '4일 누적습도',  'unit': '%',   'dir': 'down'},
    {'key': 'prcp4d', 'label': '4일 누적강수',  'unit': 'mm',  'dir': 'down'},
    {'key': 'ndmi',   'label': '식생수분 NDMI', 'unit': '',    'dir': 'down'},
]
SIG_KEYS = [s['key'] for s in SIGNALS]

t_all = time.time()
os.makedirs(WEB, exist_ok=True)

case = json.load(open(os.path.join(DERIVED, 'case_days.json'), encoding='utf-8'))
days = case['days']
if ONLY:
    keep = set(ONLY.split(','))
    days = [d for d in days if d['date'] in keep]
print(f'대상 {len(days)}일')

# ── 1. 일자별 replay 실행 ────────────────────────────────────────────
for d in days:
    ymd = d['date'].replace('-', '')
    need = [f'replay_{ymd}_grid.parquet', f'replay_{ymd}_full.npz',
            f'replay_{ymd}_summary.csv', f'replay_{ymd}_top.csv']
    # 파일 존재만 보고 건너뛰면 구버전 산출물을 그대로 쓴다. 실제로 35번 summary 에
    # max_prob·mean_prob 를 추가한 뒤, 그 이전에 만들어진 날이 스키마 불일치로 죽었다.
    # 필요한 컬럼까지 확인해서 없으면 다시 만든다.
    fresh = all(os.path.exists(os.path.join(DERIVED, f)) for f in need)
    if fresh:
        cols = pd.read_csv(os.path.join(DERIVED, f'replay_{ymd}_summary.csv'),
                           encoding='utf-8-sig', nrows=0).columns
        missing = {'max_prob', 'mean_prob'} - set(cols)
        if missing:
            print(f'  {d["date"]}: 구버전 산출물({", ".join(sorted(missing))} 없음) → 재생성')
            fresh = False
    if fresh:
        print(f'  {d["date"]}: 산출물 있음 → 건너뜀')
        continue
    print(f'  {d["date"]}: replay 실행 중...')
    env = dict(os.environ, DATE=d['date'], HOURS=HOURS, PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, '-u', os.path.join(SCRIPTS, '35_case_replay.py')],
                       env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:])
        raise SystemExit(f'{d["date"]} replay 실패')
print(f'replay 준비 완료 ({(time.time()-t_all)/60:.1f}분)')

# ── 2. 공용 셀 인덱스 (전 일자 합집합) ───────────────────────────────
with rasterio.open(MASK) as s:
    T, src_crs = s.transform, s.crs
    H, W = s.height, s.width
    bounds = s.bounds
ox, oy = T.c, T.f

grids = {}
union = set()
for d in days:
    ymd = d['date'].replace('-', '')
    g = pd.read_parquet(os.path.join(DERIVED, f'replay_{ymd}_grid.parquet'))
    g['T'] = pd.to_datetime(g['T'])
    g['hh'] = g['T'].dt.hour
    grids[ymd] = g
    sub = g.loc[g['haz_top_t1'] <= VEC_PCT, ['prow', 'pcol']].drop_duplicates()
    union |= set(zip(sub['prow'].astype(int), sub['pcol'].astype(int)))

cells = pd.DataFrame(sorted(union), columns=['prow', 'pcol']).astype({'prow': 'int32', 'pcol': 'int32'})
print(f'\n공용 셀: {len(cells):,}개 (전 {len(days)}일 합집합, 상위 {VEC_PCT}% ∩ WUI)')

exp = pd.read_parquet(os.path.join(DERIVED, 'mask_exposure_500m.parquet'))
adm = pd.read_parquet(os.path.join(DERIVED, 'cell_admin.parquet')).astype({'prow': 'int32', 'pcol': 'int32'})
cells = cells.merge(exp[['prow', 'pcol', 'pop_total', 'households', 'houses', 'low_count_only']],
                    on=['prow', 'pcol'], how='left')
cells = cells.merge(adm[['prow', 'pcol', 'adm_nm']], on=['prow', 'pcol'], how='left')

tr = pyproj.Transformer.from_crs('EPSG:5179', 'EPSG:4326', always_xy=True)
x0 = ox + 500 * cells['pcol'].values
y0 = oy - 500 * (cells['prow'].values + 1)
lon0, lat0 = tr.transform(x0, y0)
lon1, lat1 = tr.transform(x0 + 500, y0 + 500)

names = sorted({str(v) for v in cells['adm_nm'].fillna('')})
nidx = {v: i for i, v in enumerate(names)}
bbox = np.stack([lon0, lat0, lon1, lat1], axis=1)

cells_json = {
    'n': int(len(cells)),
    'b': (np.round(bbox * 1e5).astype(np.int64).ravel()).tolist(),   # 4개씩 한 셀
    'nms': names,
    'nmi': [nidx[str(v) if pd.notna(v) else ''] for v in cells['adm_nm'].fillna('')],
    'pop': [round(float(v), 1) if pd.notna(v) else 0 for v in cells['pop_total']],
    'hh': [int(v) if pd.notna(v) else 0 for v in cells['households']],
    'ho': [int(v) if pd.notna(v) else 0 for v in cells['houses']],
    'lowq': [1 if (pd.notna(v) and v) else 0 for v in cells['low_count_only']],
}
with open(os.path.join(WEB, 'cells.json'), 'w', encoding='utf-8') as f:
    json.dump(cells_json, f, ensure_ascii=False, separators=(',', ':'))
cell_index = {(int(r.prow), int(r.pcol)): i for i, r in enumerate(cells.itertuples())}
print(f'cells.json: {os.path.getsize(os.path.join(WEB, "cells.json"))/1e6:.2f} MB  (지명 {len(names)}종)')

# ── 3. 일자별 자산 ───────────────────────────────────────────────────
dst_crs = 'EPSG:3857'
dt0, dw, dh = calculate_default_transform(src_crs, dst_crs, W, H, *bounds)
dw, dh = dw // PNG_DOWNSCALE, dh // PNG_DOWNSCALE
dst_transform = rasterio.Affine(dt0.a * PNG_DOWNSCALE, 0, dt0.c, 0, dt0.e * PNG_DOWNSCALE, dt0.f)
l, tp = dst_transform.c, dst_transform.f
r_, b_ = l + dst_transform.a * dw, tp + dst_transform.e * dh
tf = pyproj.Transformer.from_crs(dst_crs, 'EPSG:4326', always_xy=True)
(lx, ty), (rx, by) = tf.transform(l, tp), tf.transform(r_, b_)
CORNERS = [[lx, ty], [rx, ty], [rx, by], [lx, by]]

cmap = LinearSegmentedColormap.from_list(
    'fire', ['#22d3ee', '#a3e635', '#facc15', '#fb923c', '#ef4444'])

fc = pd.read_parquet(os.path.join(DERIVED, 'fire_cells.parquet'))
fc['ignite_h'] = pd.to_datetime(fc['ignite_h'])
fsum = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')

index = []
for d in days:
    date, ymd = d['date'], d['date'].replace('-', '')
    out_dir = os.path.join(WEB, 'd', ymd)
    os.makedirs(out_dir, exist_ok=True)

    z = np.load(os.path.join(DERIVED, f'replay_{ymd}_full.npz'))
    prow, pcol, hours, haz = z['prow'], z['pcol'], z['hours'], z['haz_top']
    for ti in range(len(hours)):
        src_arr = np.full((H, W), np.nan, dtype=np.float32)
        src_arr[prow, pcol] = haz[ti, 0]
        dst_arr = np.full((dh, dw), np.nan, dtype=np.float32)
        reproject(src_arr, dst_arr, src_transform=T, src_crs=src_crs,
                  dst_transform=dst_transform, dst_crs=dst_crs,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
        t = np.clip(1.0 - dst_arr / TOP_PCT, 0.0, 1.0)
        t[np.isnan(dst_arr)] = 0.0
        rgba = (cmap(t) * 255).astype(np.uint8)
        a = np.where(np.isnan(dst_arr), 0, (30 + 200 * t)).astype(np.uint8)
        a[t <= 0.001] = 0
        rgba[:, :, 3] = a
        Image.fromarray(rgba, 'RGBA').save(os.path.join(out_dir, f'hazard_{hours[ti]:02d}.png'),
                                           optimize=True)

    # 용량 설계
    #   hum4d/prcp4d/ndmi 는 하루 안에서 상수(일별·8일 합성)라 셀당 1회만 저장한다.
    #   vpd/wind 만 시각마다 바뀐다. score 는 우선지역이 따로 있어 쓰지 않는다.
    #   값은 정수로 스케일해 JSON 길이를 줄인다 (top x10, 신호 x10).
    g = grids[ymd]
    daily_sig, values = {}, {}
    for hh, sub in g.groupby('hh'):
        sub = sub[sub['haz_top_t1'] <= VEC_PCT]
        idx, top, vpd, wind = [], [], [], []
        for r in sub.itertuples():
            k = cell_index.get((int(r.prow), int(r.pcol)))
            if k is None:
                continue
            idx.append(k)
            top.append(int(round(float(r.haz_top_t1) * 10)))
            vpd.append(None if pd.isna(r.vpd) else int(round(float(r.vpd) * 10)))
            wind.append(None if pd.isna(r.wind) else int(round(float(r.wind) * 10)))
            if k not in daily_sig:
                daily_sig[k] = [
                    None if pd.isna(r.hum4d) else int(round(float(r.hum4d) * 10)),
                    None if pd.isna(r.prcp4d) else int(round(float(r.prcp4d) * 10)),
                    None if pd.isna(r.ndmi) else int(round(float(r.ndmi) * 100)),
                ]
        values[str(int(hh))] = {'i': idx, 'top': top, 'vpd': vpd, 'wind': wind}
    ks = sorted(daily_sig)
    with open(os.path.join(out_dir, 'values.json'), 'w', encoding='utf-8') as f:
        json.dump({'scale': {'top': 10, 'vpd': 10, 'wind': 10,
                             'hum4d': 10, 'prcp4d': 10, 'ndmi': 100},
                   'daily': {'i': ks,
                             'hum4d': [daily_sig[k][0] for k in ks],
                             'prcp4d': [daily_sig[k][1] for k in ks],
                             'ndmi': [daily_sig[k][2] for k in ks]},
                   'hours': values}, f, separators=(',', ':'))

    tp_df = pd.read_csv(os.path.join(DERIVED, f'replay_{ymd}_top.csv'), encoding='utf-8-sig')
    tp_df['T'] = pd.to_datetime(tp_df['T'])
    tp_df['hh'] = tp_df['T'].dt.hour
    tp_df = tp_df.astype({'prow': 'int32', 'pcol': 'int32'}).merge(
        adm[['prow', 'pcol', 'adm_nm']], on=['prow', 'pcol'], how='left')
    pri = {}
    for hh, sub in tp_df.groupby('hh'):
        sub = sub.nlargest(TOP_N, 'score_t1')
        lo, la = tr.transform((ox + 500 * (sub['pcol'] + 0.5)).values,
                              (oy - 500 * (sub['prow'] + 0.5)).values)
        pri[str(int(hh))] = [{
            'i': cell_index.get((int(r.prow), int(r.pcol)), -1),
            'nm': str(r.adm_nm) if pd.notna(r.adm_nm) else '',
            'lon': round(float(a2), 5), 'lat': round(float(b2), 5),
            'top': round(float(r.haz_top_t1), 2), 'score': round(float(r.score_t1), 1),
            'pop': round(float(r.pop_total), 0), 'forest': round(float(r.forest_ratio), 2),
        } for r, a2, b2 in zip(sub.itertuples(), lo, la)]
    with open(os.path.join(out_dir, 'priority.json'), 'w', encoding='utf-8') as f:
        json.dump(pri, f, ensure_ascii=False, separators=(',', ':'))

    day_f = fc[fc['ignite_h'].dt.strftime('%Y-%m-%d') == date].drop_duplicates(['fire_id'])
    day_f = day_f.merge(fsum[['fire_id', 'loc', 'n_cells']], on='fire_id', how='left')
    fires = []
    if len(day_f):
        lo, la = tr.transform((ox + 500 * (day_f['pcol'] + 0.5)).values,
                              (oy - 500 * (day_f['prow'] + 0.5)).values)
        fires = [{'lon': round(float(a2), 5), 'lat': round(float(b2), 5),
                  'hh': int(r.ignite_h.hour), 'loc': str(r.loc),
                  'ha': float(r.damagearea), 'cells': int(r.n_cells)}
                 for r, a2, b2 in zip(day_f.itertuples(), lo, la)]
    with open(os.path.join(out_dir, 'fires.json'), 'w', encoding='utf-8') as f:
        json.dump(fires, f, ensure_ascii=False, separators=(',', ':'))

    sm = pd.read_csv(os.path.join(DERIVED, f'replay_{ymd}_summary.csv'), encoding='utf-8-sig')
    sm['hh'] = pd.to_datetime(sm['T']).dt.hour
    with open(os.path.join(out_dir, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump({'date': date, 'hours': [int(h) for h in hours],
                   'summary': {str(int(r.hh)): {
                       'top1_pop': int(r.top1pct_인구), 'top5_pop': int(r.top5pct_인구),
                       'wui_top5_cells': int(r.wui_top5pct_격자),
                       'top10_pop': int(r.top10_인구), 'n_fire': int(r.발화건수),
                       'max_prob': round(float(r.max_prob), 5),
                       'mean_prob': round(float(r.mean_prob), 6)}
                       for r in sm.itertuples()}},
                  f, ensure_ascii=False, separators=(',', ':'))

    size = sum(os.path.getsize(os.path.join(out_dir, f)) for f in os.listdir(out_dir))
    index.append({**d, 'ymd': ymd, 'hours': [int(h) for h in hours]})
    print(f'  {date}  발화 {d["n_fire"]:>2}건 {d["ha"]:>9,.1f}ha  '
          f'({d["reason"]})  {size/1e6:.1f} MB')

# ── 4. 전역 인덱스 ───────────────────────────────────────────────────
with open(os.path.join(WEB, 'days.json'), 'w', encoding='utf-8') as f:
    json.dump({'rule': case['rule'], 'image_corners': CORNERS,
               'levels': LEVELS, 'signals': SIGNALS,
               'vector_pct': VEC_PCT, 'top_pct_shown': TOP_PCT,
               'days': index,
               'note': '위험도는 확률이 아니라 전국 상대 백분위. 1:10 재표본화 학습이라 '
                       'sigmoid 출력을 발생확률로 쓸 수 없다. '
                       '신호 5종은 참고지표가 아니라 실제 모델 입력이다.'},
              f, ensure_ascii=False, separators=(',', ':'))

total = 0
for root, _, fs in os.walk(WEB):
    total += sum(os.path.getsize(os.path.join(root, f)) for f in fs)
print(f'\n전체 자산 {total/1e6:.1f} MB / {len(index)}일  ({(time.time()-t_all)/60:.1f}분)')
