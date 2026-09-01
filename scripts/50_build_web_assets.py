"""
웹 자산 생성 — 1안(배경 PNG) + 2안(상위 벡터) 혼합.

전국 403,385격자를 브라우저에 전부 올리면 무겁다. 두 층으로 나눈다.
  배경  시각별 위험 PNG (EPSG:3857로 워프) — 전국 분포를 한눈에
  벡터  WUI ∩ 어느 시각이든 위험 상위 5% 셀 — 클릭·우선순위·SGIS 노출·모델 입력값

MapLibre는 Web Mercator로 렌더링하므로 PNG를 EPSG:3857로 미리 워프해두면
image source의 네 모서리 좌표만으로 정확히 겹친다.

확률이라 부르지 않는다
  1:10 재표본화 학습이라 sigmoid 출력은 실제 발생확률이 아니다(전국 평균 0.13).
  화면에는 전국 상대 백분위와 그로 정의한 4단계 등급만 노출한다.

우측 패널의 "위험 상승 신호"는 참고지표가 아니라 실제 모델 입력이다
  VPD · 풍속 · 4일 누적습도 · 4일 누적강수 · NDMI
  (SPI-6, 토양수분은 실험에서 제외된 변수라 넣지 않는다)

출력  web/public/data/
        meta.json          시각 목록 · 이미지 모서리 · 등급 정의 · 시각별 요약
        hazard_{HH}.png    배경 위험 래스터
        cells.geojson      클릭 가능 셀 (지오메트리 + 지명 + SGIS 노출)
        cells_values.json  시각별 위험 백분위 · 점수 · 모델 입력 5종
        priority.json      시각별 대응 우선지역 Top-N (지명 포함)
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

TOP_PCT = 20.0     # 배경 PNG에 표시할 위험 상위 % (그 이하는 투명)
VEC_PCT = 5.0      # 벡터로 내보낼 위험 상위 %
TOP_N   = 10
PNG_DOWNSCALE = 2  # 워프 해상도 축소 배수 (용량 절감)

# 위험등급 — 확률이 아니라 전국 상대 백분위로 정의한다
LEVELS = [
    {'key': 'high',   'label': '매우 높음', 'max_pct': 1,   'color': '#ef4444'},
    {'key': 'mod',    'label': '높음',     'max_pct': 5,   'color': '#fb923c'},
    {'key': 'watch',  'label': '주의',     'max_pct': 10,  'color': '#facc15'},
    {'key': 'normal', 'label': '보통',     'max_pct': 20,  'color': '#a3e635'},
]

os.makedirs(OUT, exist_ok=True)

# ── 1. 전체 격자 → 시각별 PNG (EPSG:3857 워프) ───────────────────────
z = np.load(os.path.join(DERIVED, f'replay_{YMD}_full.npz'))
prow, pcol, hours, haz = z['prow'], z['pcol'], z['hours'], z['haz_top']
n_t = len(hours)
print(f'전체 격자: 시각 {n_t}개 × 픽셀 {len(prow):,}')

with rasterio.open(MASK) as s:
    src_transform, src_crs = s.transform, s.crs
    H, W = s.height, s.width
    bounds = s.bounds

dst_crs = 'EPSG:3857'
dst_transform, dw, dh = calculate_default_transform(src_crs, dst_crs, W, H, *bounds)
dw, dh = dw // PNG_DOWNSCALE, dh // PNG_DOWNSCALE
dst_transform = rasterio.Affine(dst_transform.a * PNG_DOWNSCALE, 0, dst_transform.c,
                                0, dst_transform.e * PNG_DOWNSCALE, dst_transform.f)
print(f'워프 대상: {dw} × {dh} (EPSG:3857, 1/{PNG_DOWNSCALE} 축소)')

cmap = LinearSegmentedColormap.from_list(
    'fire', ['#22d3ee', '#a3e635', '#facc15', '#fb923c', '#ef4444'])

corners = None
for ti in range(n_t):
    src_arr = np.full((H, W), np.nan, dtype=np.float32)
    src_arr[prow, pcol] = haz[ti, 0]
    dst_arr = np.full((dh, dw), np.nan, dtype=np.float32)
    reproject(src_arr, dst_arr, src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_transform, dst_crs=dst_crs,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)

    t = np.clip(1.0 - dst_arr / TOP_PCT, 0.0, 1.0)
    t[np.isnan(dst_arr)] = 0.0
    rgba = (cmap(t) * 255).astype(np.uint8)
    alpha = np.where(np.isnan(dst_arr), 0, (30 + 200 * t)).astype(np.uint8)
    alpha[t <= 0.001] = 0
    rgba[:, :, 3] = alpha
    Image.fromarray(rgba, 'RGBA').save(os.path.join(OUT, f'hazard_{hours[ti]:02d}.png'), optimize=True)

    if corners is None:
        l, tp = dst_transform.c, dst_transform.f
        r, b = l + dst_transform.a * dw, tp + dst_transform.e * dh
        tf = pyproj.Transformer.from_crs(dst_crs, 'EPSG:4326', always_xy=True)
        (lx, ty), (rx, by) = tf.transform(l, tp), tf.transform(r, b)
        corners = [[lx, ty], [rx, ty], [rx, by], [lx, by]]
print(f'PNG {n_t}장')

# ── 2. 클릭 가능 벡터 셀 ─────────────────────────────────────────────
g = pd.read_parquet(os.path.join(DERIVED, f'replay_{YMD}_grid.parquet'))
g['T'] = pd.to_datetime(g['T'])
g['hh'] = g['T'].dt.hour

sel = g.loc[g['haz_top_t1'] <= VEC_PCT, ['prow', 'pcol']].drop_duplicates()
print(f'\n벡터 셀 (어느 시각이든 상위 {VEC_PCT}% ∩ WUI): {len(sel):,}개')

exp = pd.read_parquet(os.path.join(DERIVED, 'mask_exposure_500m.parquet'))
adm = pd.read_parquet(os.path.join(DERIVED, 'cell_admin.parquet')).astype({'prow': 'int32', 'pcol': 'int32'})
sel = sel.astype({'prow': 'int32', 'pcol': 'int32'})
sel = sel.merge(exp[['prow', 'pcol', 'pop_total', 'households', 'houses', 'low_count_only']],
                on=['prow', 'pcol'], how='left')
sel = sel.merge(adm[['prow', 'pcol', 'adm_cd', 'adm_nm']], on=['prow', 'pcol'], how='left')

with rasterio.open(MASK) as s:
    T = s.transform
ox, oy = T.c, T.f
tr5179 = pyproj.Transformer.from_crs('EPSG:5179', 'EPSG:4326', always_xy=True)

feats, cell_index = [], {}
for i, r in enumerate(sel.itertuples()):
    x0, y0 = ox + 500 * r.pcol, oy - 500 * (r.prow + 1)
    xs = [x0, x0 + 500, x0 + 500, x0, x0]
    ys = [y0 + 500, y0 + 500, y0, y0, y0 + 500]
    lon, lat = tr5179.transform(xs, ys)
    feats.append({
        'type': 'Feature', 'id': i,
        'geometry': {'type': 'Polygon',
                     'coordinates': [[[round(a, 5), round(b, 5)] for a, b in zip(lon, lat)]]},
        'properties': {
            'i': i,
            'nm': str(r.adm_nm) if pd.notna(r.adm_nm) else '',
            'cd': str(r.adm_cd) if pd.notna(r.adm_cd) else '',
            'pop': round(float(r.pop_total), 1),
            'hh_': int(r.households) if pd.notna(r.households) else 0,
            'ho': int(r.houses) if pd.notna(r.houses) else 0,
            'lowq': bool(r.low_count_only) if pd.notna(r.low_count_only) else True,
        }})
    cell_index[(int(r.prow), int(r.pcol))] = i

with open(os.path.join(OUT, 'cells.geojson'), 'w', encoding='utf-8') as f:
    json.dump({'type': 'FeatureCollection', 'features': feats}, f, ensure_ascii=False, separators=(',', ':'))
print(f'cells.geojson: {len(feats):,} 피처 (지명 포함)')

# ── 3. 시각별 값 + 모델 입력 5종 ─────────────────────────────────────
SIG = ['vpd', 'wind', 'hum4d', 'prcp4d', 'ndmi']
values = {}
for hh, sub in g.groupby('hh'):
    sub = sub[sub['haz_top_t1'] <= VEC_PCT]
    idx, top, score = [], [], []
    sig = {c: [] for c in SIG}
    for r in sub.itertuples():
        k = cell_index.get((int(r.prow), int(r.pcol)))
        if k is None:
            continue
        idx.append(k)
        top.append(round(float(r.haz_top_t1), 3))
        score.append(round(float(r.score_t1), 2) if pd.notna(r.score_t1) else None)
        for c in SIG:
            v = getattr(r, c)
            sig[c].append(round(float(v), 2) if pd.notna(v) else None)
    values[str(int(hh))] = {'i': idx, 'top': top, 'score': score, **sig}
with open(os.path.join(OUT, 'cells_values.json'), 'w', encoding='utf-8') as f:
    json.dump(values, f, separators=(',', ':'))
print(f'cells_values.json: 시각 {len(values)}개 (모델 입력 {len(SIG)}종 포함)')

# ── 4. 우선지역 Top-N ────────────────────────────────────────────────
top_df = pd.read_csv(os.path.join(DERIVED, f'replay_{YMD}_top.csv'), encoding='utf-8-sig')
top_df['T'] = pd.to_datetime(top_df['T'])
top_df['hh'] = top_df['T'].dt.hour
top_df = top_df.astype({'prow': 'int32', 'pcol': 'int32'}).merge(
    adm[['prow', 'pcol', 'adm_nm']], on=['prow', 'pcol'], how='left')

pri = {}
for hh, sub in top_df.groupby('hh'):
    sub = sub.nlargest(TOP_N, 'score_t1')
    lon, lat = tr5179.transform((ox + 500 * (sub['pcol'] + 0.5)).values,
                                (oy - 500 * (sub['prow'] + 0.5)).values)
    pri[str(int(hh))] = [{
        'i': cell_index.get((int(r.prow), int(r.pcol)), -1),
        'nm': str(r.adm_nm) if pd.notna(r.adm_nm) else '',
        'lon': round(float(a), 5), 'lat': round(float(b), 5),
        'top': round(float(r.haz_top_t1), 2), 'score': round(float(r.score_t1), 1),
        'pop': round(float(r.pop_total), 0), 'forest': round(float(r.forest_ratio), 2),
    } for r, a, b in zip(sub.itertuples(), lon, lat)]
with open(os.path.join(OUT, 'priority.json'), 'w', encoding='utf-8') as f:
    json.dump(pri, f, ensure_ascii=False, separators=(',', ':'))
print(f'priority.json: 시각 {len(pri)}개 × Top-{TOP_N} (지명 포함)')

# ── 5. 실제 발화점 ───────────────────────────────────────────────────
fc = pd.read_parquet(os.path.join(DERIVED, 'fire_cells.parquet'))
fc['ignite_h'] = pd.to_datetime(fc['ignite_h'])
day = fc[fc['ignite_h'].dt.strftime('%Y-%m-%d') == DATE].drop_duplicates(['fire_id'])
summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
day = day.merge(summ[['fire_id', 'loc', 'n_cells']], on='fire_id', how='left')
fires = []
if len(day):
    lon, lat = tr5179.transform((ox + 500 * (day['pcol'] + 0.5)).values,
                                (oy - 500 * (day['prow'] + 0.5)).values)
    fires = [{'lon': round(float(a), 5), 'lat': round(float(b), 5), 'hh': int(r.ignite_h.hour),
              'loc': str(r.loc), 'ha': float(r.damagearea), 'cells': int(r.n_cells)}
             for r, a, b in zip(day.itertuples(), lon, lat)]
with open(os.path.join(OUT, 'fires.json'), 'w', encoding='utf-8') as f:
    json.dump(fires, f, ensure_ascii=False, separators=(',', ':'))
print(f'fires.json: {len(fires)}건')

# ── 6. 메타 ──────────────────────────────────────────────────────────
sm = pd.read_csv(os.path.join(DERIVED, f'replay_{YMD}_summary.csv'), encoding='utf-8-sig')
sm['hh'] = pd.to_datetime(sm['T']).dt.hour
meta = {
    'date': DATE,
    'hours': [int(h) for h in hours],
    'image_corners': corners,
    'top_pct_shown': TOP_PCT,
    'vector_pct': VEC_PCT,
    'levels': LEVELS,
    'signals': [
        {'key': 'vpd',    'label': '증기압차 VPD', 'unit': 'hPa', 'dir': 'up'},
        {'key': 'wind',   'label': '풍속',        'unit': 'm/s', 'dir': 'up'},
        {'key': 'hum4d',  'label': '4일 누적습도', 'unit': '%',   'dir': 'down'},
        {'key': 'prcp4d', 'label': '4일 누적강수', 'unit': 'mm',  'dir': 'down'},
        {'key': 'ndmi',   'label': '식생수분 NDMI', 'unit': '',   'dir': 'down'},
    ],
    'summary': {str(int(r.hh)): {
        'top1_pop': int(r.top1pct_인구), 'top5_pop': int(r.top5pct_인구),
        'wui_top5_cells': int(r.wui_top5pct_격자), 'top10_pop': int(r.top10_인구),
        'n_fire': int(r.발화건수),
    } for r in sm.itertuples()},
    'note': '위험도는 확률이 아니라 전국 상대 백분위. 1:10 재표본화 학습이라 '
            'sigmoid 출력을 발생확률로 쓸 수 없다. 신호 5종은 참고지표가 아니라 실제 모델 입력이다.',
}
with open(os.path.join(OUT, 'meta.json'), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, separators=(',', ':'))

tot = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT)
          if os.path.isfile(os.path.join(OUT, f)))
print(f'\n총 자산 {tot/1e6:.2f} MB')
for f in sorted(os.listdir(OUT)):
    fp = os.path.join(OUT, f)
    if os.path.isfile(fp):
        print(f'  {f:<24} {os.path.getsize(fp)/1e3:>8,.1f} KB')
