"""
웹 MVP 자산 생성 — 1안(배경 PNG) + 2안(상위 벡터) 혼합.

전국 403,385격자를 브라우저에 전부 올리면 무겁다. 두 층으로 나눈다.
  배경  시각별 위험 PNG (EPSG:3857로 워프) — 전국 분포를 한눈에
  벡터  WUI ∩ 어느 시각이든 위험 상위 5% 셀 — 클릭·우선순위·SGIS 노출

MapLibre는 Web Mercator로 렌더링하므로 PNG를 EPSG:3857로 미리 워프해두면
image source의 네 모서리 좌표만으로 정확히 겹친다.

입력  derived/replay_{ymd}_full.npz      전체 격자 위험 백분위 (시각 × 3 × 픽셀)
      derived/replay_{ymd}_grid.parquet  WUI 격자 시각별 값
      derived/replay_{ymd}_summary.csv   시각별 요약 + 실제 발화
      derived/mask_exposure_500m.parquet SGIS 노출
      derived/fire_cells.parquet         화재 셀 (실제 발화 오버레이용)

출력  web/public/data/
        meta.json          시각 목록 · 이미지 모서리 좌표 · 범례 · 시각별 요약
        hazard_{HH}.png    배경 위험 래스터 13장
        cells.geojson      클릭 가능 셀 (지오메트리 + 정적 속성)
        cells_values.json  시각별 위험 백분위 · 우선순위 점수
        priority.json      시각별 대응 우선지역 Top-10
        fires.json         당일 실제 발화점
"""

import os, json
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image
import pyproj

DERIVED = r'C:\for_sgis\data\grid_data\derived'
MASK    = r'V:\data\mask\common_mask_500m_5179.tif'
OUT     = r'C:\for_sgis\web\public\data'
DATE    = os.environ.get('DATE', '2025-03-22')
YMD     = DATE.replace('-', '')

TOP_PCT   = 20.0     # 배경 PNG에 표시할 위험 상위 % (그 이하는 투명)
VEC_PCT   = 5.0      # 벡터로 내보낼 위험 상위 %
TOP_N     = 10

os.makedirs(OUT, exist_ok=True)

# ── 1. 전체 격자 → 시각별 PNG (EPSG:3857 워프) ───────────────────────
z = np.load(os.path.join(DERIVED, f'replay_{YMD}_full.npz'))
prow, pcol, hours, haz = z['prow'], z['pcol'], z['hours'], z['haz_top']
n_t = len(hours)
print(f'전체 격자 로드: 시각 {n_t}개 × 픽셀 {len(prow):,}')

with rasterio.open(MASK) as s:
    src_transform, src_crs = s.transform, s.crs
    H, W = s.height, s.width
    bounds = s.bounds

dst_crs = 'EPSG:3857'
dst_transform, dst_w, dst_h = calculate_default_transform(
    src_crs, dst_crs, W, H, *bounds)
print(f'워프 대상: {dst_w} × {dst_h} (EPSG:3857)')

# 위험도 → 색: 상위일수록 붉고 진하게. 어두운 지도 위에 얹을 것이므로 저위험은 투명.
cmap = LinearSegmentedColormap.from_list(
    'fire', ['#22d3ee', '#a3e635', '#facc15', '#fb923c', '#ef4444'])

corners_ll = None
for ti in range(n_t):
    src_arr = np.full((H, W), np.nan, dtype=np.float32)
    src_arr[prow, pcol] = haz[ti, 0]          # t+1h 기준

    dst_arr = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
    reproject(src_arr, dst_arr,
              src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_transform, dst_crs=dst_crs,
              src_nodata=np.nan, dst_nodata=np.nan,
              resampling=Resampling.bilinear)

    # haz_top: 0 = 전국 1위. 상위 TOP_PCT 안만 그린다.
    t = np.clip(1.0 - dst_arr / TOP_PCT, 0.0, 1.0)
    t[np.isnan(dst_arr)] = 0.0
    rgba = (cmap(t) * 255).astype(np.uint8)
    alpha = np.where(np.isnan(dst_arr), 0, (30 + 200 * t)).astype(np.uint8)
    alpha[t <= 0.001] = 0
    rgba[:, :, 3] = alpha

    Image.fromarray(rgba, 'RGBA').save(os.path.join(OUT, f'hazard_{hours[ti]:02d}.png'),
                                       optimize=True)
    if corners_ll is None:
        l, t_, r, b = (dst_transform.c, dst_transform.f,
                       dst_transform.c + dst_transform.a * dst_w,
                       dst_transform.f + dst_transform.e * dst_h)
        tr = pyproj.Transformer.from_crs(dst_crs, 'EPSG:4326', always_xy=True)
        (lx, ty), (rx, by) = tr.transform(l, t_), tr.transform(r, b)
        corners_ll = [[lx, ty], [rx, ty], [rx, by], [lx, by]]
        print(f'이미지 모서리(lon,lat): {np.round(corners_ll, 4).tolist()}')

print(f'PNG {n_t}장 생성')

# ── 2. 클릭 가능 벡터 셀 ─────────────────────────────────────────────
g = pd.read_parquet(os.path.join(DERIVED, f'replay_{YMD}_grid.parquet'))
g['T'] = pd.to_datetime(g['T'])
g['hh'] = g['T'].dt.hour

sel = g.loc[g['haz_top_t1'] <= VEC_PCT, ['prow', 'pcol']].drop_duplicates()
print(f'\n벡터 셀: 어느 시각이든 위험 상위 {VEC_PCT}% ∩ WUI → {len(sel):,}개')

exp = pd.read_parquet(os.path.join(DERIVED, 'mask_exposure_500m.parquet'))
sel = sel.merge(exp[['prow', 'pcol', 'pop_total', 'households', 'houses', 'low_count_only']],
                on=['prow', 'pcol'], how='left')
static_cols = g.drop_duplicates(['prow', 'pcol'])[['prow', 'pcol', 'pop_total']]
sel = sel.merge(g.drop_duplicates(['prow', 'pcol'])[['prow', 'pcol']], on=['prow', 'pcol'])

tr5179 = pyproj.Transformer.from_crs('EPSG:5179', 'EPSG:4326', always_xy=True)
ox, oy = src_transform.c, src_transform.f
feats = []
cell_index = {}
for i, r in enumerate(sel.itertuples()):
    x0 = ox + 500 * r.pcol
    y0 = oy - 500 * (r.prow + 1)
    xs = [x0, x0 + 500, x0 + 500, x0, x0]
    ys = [y0 + 500, y0 + 500, y0, y0, y0 + 500]
    lon, lat = tr5179.transform(xs, ys)
    feats.append({
        'type': 'Feature',
        'id': i,
        'geometry': {'type': 'Polygon',
                     'coordinates': [[[round(a, 5), round(b, 5)] for a, b in zip(lon, lat)]]},
        'properties': {
            'i': i,
            'pop': round(float(r.pop_total), 1),
            'hh_': int(r.households) if pd.notna(r.households) else 0,
            'ho': int(r.houses) if pd.notna(r.houses) else 0,
            'lowq': bool(r.low_count_only) if pd.notna(r.low_count_only) else True,
        }})
    cell_index[(r.prow, r.pcol)] = i

with open(os.path.join(OUT, 'cells.geojson'), 'w', encoding='utf-8') as f:
    json.dump({'type': 'FeatureCollection', 'features': feats}, f, separators=(',', ':'))
print(f'cells.geojson: {len(feats):,} 피처')

# ── 3. 시각별 값 (셀 인덱스 → 위험 백분위 · 점수) ────────────────────
values = {}
for hh, sub in g.groupby('hh'):
    sub = sub[sub['haz_top_t1'] <= VEC_PCT]
    idx, htop, score = [], [], []
    for r in sub.itertuples():
        k = cell_index.get((r.prow, r.pcol))
        if k is None:
            continue
        idx.append(k)
        htop.append(round(float(r.haz_top_t1), 3))
        score.append(round(float(r.score_t1), 2) if pd.notna(r.score_t1) else None)
    values[str(int(hh))] = {'i': idx, 'top': htop, 'score': score}
with open(os.path.join(OUT, 'cells_values.json'), 'w', encoding='utf-8') as f:
    json.dump(values, f, separators=(',', ':'))
print(f'cells_values.json: 시각 {len(values)}개')

# ── 4. 우선지역 Top-N ────────────────────────────────────────────────
top = pd.read_csv(os.path.join(DERIVED, f'replay_{YMD}_top.csv'), encoding='utf-8-sig')
top['T'] = pd.to_datetime(top['T'])
top['hh'] = top['T'].dt.hour
pri = {}
for hh, sub in top.groupby('hh'):
    sub = sub.nlargest(TOP_N, 'score_t1')
    lon, lat = tr5179.transform((ox + 500 * (sub['pcol'] + 0.5)).values,
                                (oy - 500 * (sub['prow'] + 0.5)).values)
    pri[str(int(hh))] = [{
        'i': cell_index.get((int(r.prow), int(r.pcol)), -1),
        'lon': round(float(a), 5), 'lat': round(float(b), 5),
        'top': round(float(r.haz_top_t1), 2),
        'score': round(float(r.score_t1), 1),
        'pop': round(float(r.pop_total), 0),
        'forest': round(float(r.forest_ratio), 2),
    } for r, a, b in zip(sub.itertuples(), lon, lat)]
with open(os.path.join(OUT, 'priority.json'), 'w', encoding='utf-8') as f:
    json.dump(pri, f, separators=(',', ':'))
print(f'priority.json: 시각 {len(pri)}개 × Top-{TOP_N}')

# ── 5. 실제 발화점 ───────────────────────────────────────────────────
fc = pd.read_parquet(os.path.join(DERIVED, 'fire_cells.parquet'))
fc['ignite_h'] = pd.to_datetime(fc['ignite_h'])
day = fc[fc['ignite_h'].dt.strftime('%Y-%m-%d') == DATE].drop_duplicates(['fire_id'])
summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
day = day.merge(summ[['fire_id', 'loc', 'n_cells']], on='fire_id', how='left')
if len(day):
    lon, lat = tr5179.transform((ox + 500 * (day['pcol'] + 0.5)).values,
                                (oy - 500 * (day['prow'] + 0.5)).values)
    fires = [{'lon': round(float(a), 5), 'lat': round(float(b), 5),
              'hh': int(r.ignite_h.hour), 'loc': str(r.loc),
              'ha': float(r.damagearea), 'cells': int(r.n_cells)}
             for r, a, b in zip(day.itertuples(), lon, lat)]
else:
    fires = []
with open(os.path.join(OUT, 'fires.json'), 'w', encoding='utf-8') as f:
    json.dump(fires, f, ensure_ascii=False, separators=(',', ':'))
print(f'fires.json: {len(fires)}건')

# ── 6. 메타 ──────────────────────────────────────────────────────────
sm = pd.read_csv(os.path.join(DERIVED, f'replay_{YMD}_summary.csv'), encoding='utf-8-sig')
sm['hh'] = pd.to_datetime(sm['T']).dt.hour
meta = {
    'date': DATE,
    'hours': [int(h) for h in hours],
    'image_corners': corners_ll,
    'top_pct_shown': TOP_PCT,
    'vector_pct': VEC_PCT,
    'legend': [
        {'label': '상위 1% 이내', 'color': '#ef4444'},
        {'label': '상위 5% 이내', 'color': '#fb923c'},
        {'label': '상위 10% 이내', 'color': '#facc15'},
        {'label': '상위 20% 이내', 'color': '#a3e635'},
    ],
    'summary': {str(int(r.hh)): {
        'top1_pop': int(r.top1pct_인구), 'top5_pop': int(r.top5pct_인구),
        'wui_top5_cells': int(r.wui_top5pct_격자),
        'top10_pop': int(r.top10_인구),
        'top10_haz': float(r._7) if hasattr(r, '_7') else None,
        'n_fire': int(r.발화건수),
    } for r in sm.itertuples()},
    'note': '위험도는 확률이 아니라 전국 상대 백분위. 1:10 재표본화 학습이라 sigmoid 출력을 발생확률로 쓸 수 없다.',
}
with open(os.path.join(OUT, 'meta.json'), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, separators=(',', ':'))

tot = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
print(f'\n총 자산 크기: {tot/1e6:.2f} MB')
for f in sorted(os.listdir(OUT)):
    print(f'  {f:<24} {os.path.getsize(os.path.join(OUT, f))/1e3:>8,.1f} KB')
