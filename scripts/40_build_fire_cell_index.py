"""
화재 셀-시간 인덱스 — 인수인계_산불라벨_필터링_방법론.md (2026-08-31) 기준 재구현.

라벨 정의
  양성 = [화재별 픽셀집합] × [화재별 시간범위]

픽셀집합
  폴리곤 보유(신고 >= 25ha) → 거리필터 통과 폴리곤을 all_touched=False(셀 중심)로 래스터화
  폴리곤 없음 또는 필터 후 0개 → 발화점 1픽셀

  ※ all_touched=False가 실제 채택 구현이다. README 구버전 §5의 "교차하면 1"은
    2026-08-31 개정판에서 정정됐다. all_touched=True는 울진에서 5,988.8ha → 20,050ha(3.3배),
    전체로는 19,946ha → 81,875ha(4.1배)로 부풀린다. 울진 폴리곤 426개 중 92.7%가
    25ha(=1셀) 미만 파편이라 조각마다 셀이 켜지기 때문.

시간범위
  ignite_h ~ min(종료시각, ignite_h + 24h)          [필터②]

사건 선별
  2021~2025, 2~6월, 0 < (종료 - 시작) <= 720h        [필터③]
  ※ 기존 72h 컷오프는 울진(222.7h)·산청(532.6h) 등 실제 대형산불을 배제했다.
    720h 초과는 fire_id 5687(종료연도 2055 오타) 1건뿐.

거리필터 [필터①]
  등가반경_km = sqrt(신고면적_ha * 10000 / pi) / 1000
  임계거리_km = max(5.0, 등가반경_km * 3)
  폴리곤 중심점이 발화점에서 임계거리 이내인 것만 채택

  계수 3 근거: 발화점을 타원의 후단 초점으로 두는 표준 확산모델(Scott 2012)에서
    최대거리 = r·√k(1+√(1−1/k²)), k는 장단축비.
    FBP LB = 1.0 + 8.729(1−e^(−0.030W))^2.155 (FCFDG 1992 Eq.79)에
    관측 풍속 99백분위(20.9 km/h)를 넣으면 배수 약 3.1.
  5km 하한 근거: 발화점 좌표가 GPS가 아니라 읍면동/리 지오코딩이라 수 km 오차가 정상.

입력은 전부 읽기 전용 (NAS 원본 미변경, 2026-09-01 15:07 작업폴더 스냅샷 사용).

출력
  fire_cells.parquet            fire_id, prow, pcol, source
  fire_cell_hours.parquet       fire_id, prow, pcol, dt  (시간 전개)
  fire_cell_summary.csv         사건별 요약
  polygon_distance_filter_log.csv  거리필터 상세
"""

import os, glob, re
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import rowcol
import pyproj

REF      = r'C:\for_sgis\data\fire_reference'
DERIVED  = r'C:\for_sgis\data\grid_data\derived'
MASK     = r'V:\data\mask\common_mask_500m_5179.tif'
POLY_DIR = os.path.join(REF, 'burned_area_polygons_masked')

CELL_HA       = 25.0
MAX_POS_HOURS = 24            # 필터②
MAX_DURATION  = 720           # 필터③ (시간)
DIST_COEF     = 3.0           # 필터① 계수
DIST_FLOOR_KM = 5.0           # 필터① 하한
YEARS         = (2021, 2025)
MONTHS        = [2, 3, 4, 5, 6]
DATA_CAP_2025 = pd.Timestamp('2025-06-26 06:00:00')

with rasterio.open(MASK) as s:
    mask_arr, transform = s.read(1), s.transform
    shape = (s.height, s.width)
print(f'격자 {shape}  유효 {int((mask_arr == 1).sum()):,}셀')

# ── 사건 목록 ────────────────────────────────────────────────────────
geo = pd.read_csv(os.path.join(REF, 'fire_events_geocoded.csv'), encoding='utf-8-sig')
raw = pd.read_csv(os.path.join(REF, 'fire_raw_2015_2025.csv'), encoding='utf-8-sig')
geo['start_dt'] = pd.to_datetime(geo['datetime'])
# 두 CSV는 행 순서가 동일하다(시작시각 5,819건 100% 일치 확인) → 위치 기반 대입이 안전
geo['end_dt'] = pd.to_datetime(
    raw['endyear'].astype(str) + '-' + raw['endmonth'].astype(str).str.zfill(2) + '-' +
    raw['endday'].astype(str).str.zfill(2) + ' ' + raw['endtime'].astype(str), errors='coerce').values

dur_h = (geo['end_dt'] - geo['start_dt']).dt.total_seconds() / 3600
n0 = len(geo)
geo = geo[(geo['start_dt'].dt.year.between(*YEARS)) &
          (geo['start_dt'].dt.month.isin(MONTHS)) &
          (dur_h > 0) & (dur_h <= MAX_DURATION)].copy()
print(f'사건 선별: {n0:,} → {len(geo):,}건  (시즌 + 0<지속<={MAX_DURATION}h)')

# 좌표: 원본 + SGIS 복구
tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)
gx, gy = tr.transform(geo['lon'].values, geo['lat'].values)
geo['x_5179'], geo['y_5179'] = gx, gy
geo['coord_src'] = np.where(geo['lon'].notna(), 'original', None)

rec_path = os.path.join(DERIVED, 'fire_events_geocode_recovered.csv')
if os.path.exists(rec_path):
    rec = pd.read_csv(rec_path, encoding='utf-8-sig')
    rec = rec[rec['recover_level'].isin(['ri', 'dong'])]
    m = geo['x_5179'].isna()
    fill = geo.loc[m, ['fire_id']].merge(rec[['fire_id', 'x_5179', 'y_5179']],
                                         on='fire_id', how='left')
    geo.loc[m, 'x_5179'] = fill['x_5179'].values
    geo.loc[m, 'y_5179'] = fill['y_5179'].values
    geo.loc[m, 'coord_src'] = np.where(fill['x_5179'].notna(), 'sgis_recovered', None)
    print(f'  SGIS 복구 좌표 반영: {int(fill["x_5179"].notna().sum()):,}건')

geo = geo[geo['x_5179'].notna()].copy()
geo['ignite_h'] = geo['start_dt'].dt.floor('h')
geo = geo[~((geo['ignite_h'].dt.year == 2025) & (geo['ignite_h'] > DATA_CAP_2025))].copy()
print(f'좌표 확보: {len(geo):,}건')

poly_map = {}
for f in glob.glob(os.path.join(POLY_DIR, '*.gpkg')):
    mm = re.match(r'fire_(\d+)_(\d{8})\.gpkg', os.path.basename(f))
    if mm:
        poly_map[int(mm.group(1))] = f
print(f'마스킹 폴리곤: {len(poly_map)}건')

# ── 셀 산출 ──────────────────────────────────────────────────────────
rows, summary, distlog = [], [], []
n_poly, n_point, n_poly_dropped = 0, 0, 0

for r in geo.itertuples():
    fid = int(r.fire_id)
    area = float(r.damagearea) if pd.notna(r.damagearea) else 0.0
    cells, src = None, None

    if area >= CELL_HA and fid in poly_map:
        g = gpd.read_file(poly_map[fid])
        if g.crs is not None and g.crs.to_epsg() != 5179:
            g = g.to_crs(5179)
        g = g[g.geometry.notna() & ~g.geometry.is_empty]

        r_eq_km = np.sqrt(area * 10000 / np.pi) / 1000
        thr_km = max(DIST_FLOOR_KM, r_eq_km * DIST_COEF)
        cen = g.geometry.centroid
        dist_km = np.sqrt((cen.x - r.x_5179) ** 2 + (cen.y - r.y_5179) ** 2) / 1000
        keep = g[dist_km <= thr_km]

        distlog.append({'fire_id': fid, 'damagearea': area,
                        'r_eq_km': round(r_eq_km, 2), 'thr_km': round(thr_km, 2),
                        'n_poly_all': len(g), 'ha_all': round(g.geometry.area.sum() / 1e4, 1),
                        'n_poly_keep': len(keep),
                        'ha_keep': round(keep.geometry.area.sum() / 1e4, 1) if len(keep) else 0.0,
                        'dist_median_km': round(float(np.median(dist_km)), 2) if len(g) else np.nan,
                        'floor_decided': thr_km == DIST_FLOOR_KM})

        if len(keep):
            arr = rasterize([(x, 1) for x in keep.geometry], out_shape=shape,
                            transform=transform, fill=0, all_touched=False, dtype=np.uint8)
            rr, cc = np.where((arr == 1) & (mask_arr == 1))
            if len(rr):
                cells = list(zip(rr.tolist(), cc.tolist()))
                src = 'polygon'
                n_poly += 1
        if cells is None:
            n_poly_dropped += 1

    if cells is None:
        pr, pc = rowcol(transform, r.x_5179, r.y_5179)
        if 0 <= pr < shape[0] and 0 <= pc < shape[1] and mask_arr[pr, pc] == 1:
            cells = [(int(pr), int(pc))]
            src = 'point'
            n_point += 1

    if not cells:
        continue

    end_h = min(r.end_dt.floor('h'), r.ignite_h + pd.Timedelta(hours=MAX_POS_HOURS))
    for pr, pc in cells:
        rows.append({'fire_id': fid, 'prow': pr, 'pcol': pc, 'source': src,
                     'ignite_h': r.ignite_h, 'end_h': end_h, 'damagearea': area})
    summary.append({'fire_id': fid, 'ignite_h': r.ignite_h, 'end_h': end_h,
                    'hours': int((end_h - r.ignite_h).total_seconds() // 3600) + 1,
                    'damagearea': area, 'source': src, 'n_cells': len(cells),
                    'coord_src': r.coord_src,
                    'loc': f'{r.locsi} {r.locgungu} {r.locmenu}'})

cells_df = pd.DataFrame(rows)
sum_df = pd.DataFrame(summary)
pd.DataFrame(distlog).to_csv(os.path.join(DERIVED, 'polygon_distance_filter_log.csv'),
                             index=False, encoding='utf-8-sig')

# ── 시간 전개 ────────────────────────────────────────────────────────
exp = []
for r in cells_df.itertuples():
    for dt in pd.date_range(r.ignite_h, r.end_h, freq='h'):
        if dt.month in MONTHS and not (dt.year == 2025 and dt > DATA_CAP_2025):
            exp.append((r.fire_id, r.prow, r.pcol, dt))
hours_df = pd.DataFrame(exp, columns=['fire_id', 'prow', 'pcol', 'dt'])

cells_df.to_parquet(os.path.join(DERIVED, 'fire_cells.parquet'), index=False)
hours_df.to_parquet(os.path.join(DERIVED, 'fire_cell_hours.parquet'), index=False)
sum_df.to_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), index=False, encoding='utf-8-sig')

print(f'\n{"="*74}')
print(f'사건 {len(sum_df):,}건 → 화재 셀 {len(cells_df):,}개 → 셀×시각 {len(hours_df):,}행')
print(f'  폴리곤 기반 {n_poly}건 / 발화점 1셀 {n_point:,}건 / 거리필터로 폴리곤 폐기 {n_poly_dropped}건')
print(f'  폴리곤 사건 셀 합계: {int(sum_df.loc[sum_df["source"]=="polygon","n_cells"].sum()):,}'
      f'  (방법론 문서 기준값 407)')

dl = pd.DataFrame(distlog)
if len(dl):
    print(f'\n거리필터: 적용 {len(dl)}건 중 5km 하한이 결정한 사건 {int(dl["floor_decided"].sum())}건')
    print(f'  폴리곤 면적 {dl["ha_all"].sum():,.0f}ha → 채택 {dl["ha_keep"].sum():,.0f}ha '
          f'({100*dl["ha_keep"].sum()/dl["ha_all"].sum():.1f}%)')
    print('\n  주요 사건:')
    key = dl.nlargest(8, 'ha_all')[['fire_id', 'damagearea', 'thr_km', 'n_poly_all',
                                    'ha_all', 'n_poly_keep', 'ha_keep', 'dist_median_km']]
    print(key.to_string(index=False))

print(f'\n시간범위 (24h 상한 적용):')
print(f'  중앙값 {sum_df["hours"].median():.0f}h  평균 {sum_df["hours"].mean():.1f}h  '
      f'최대 {sum_df["hours"].max()}h')
print(f'  상한에 걸린 사건: {int((sum_df["hours"] > MAX_POS_HOURS).sum()):,}건 '
      f'(=25h 표기, 24h 상한+시작시각 포함)')

sum_df['year'] = sum_df['ignite_h'].dt.year
print(f'\n연도별:')
print(sum_df.groupby('year').agg(사건=('fire_id', 'size'), 셀=('n_cells', 'sum'),
                                 폴리곤사건=('source', lambda s: (s == 'polygon').sum())).to_string())
print(f'\n폴리곤 기반 상위 8건:')
print(sum_df[sum_df['source'] == 'polygon'].nlargest(8, 'n_cells')
      [['fire_id', 'loc', 'damagearea', 'n_cells', 'hours']].to_string(index=False))
