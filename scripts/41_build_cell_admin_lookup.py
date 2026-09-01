"""
격자 → 행정동 대응표 — SGIS 통계지역경계(BND_ADM_DONG_PG) 기준.

왜 필요한가
  1) 지금까지 Top-N 지명을 SGIS 역지오코딩 API로 붙였다. 호출당 왕복이 있어 전 격자에는 못 쓴다.
     경계를 한 번 래스터화해두면 403,385셀 전부에 지명이 오프라인으로 붙는다.
  2) 읍면동 단위 집계가 가능해진다. "구미시 도량동 격자 3개가 상위 1%"처럼
     행정 단위로 말해야 지자체 담당자가 바로 쓸 수 있다.
  3) ADM_CD가 SGIS 8자리 행정동 코드라, SGIS OpenAPI(stats/population 등)의
     adm_cd와 그대로 조인된다. 격자 → 행정동 → SGIS 지역통계 경로가 열린다.

좌표계 주의
  경계 SHP는 EPSG:5186 (KGD2002 중부원점 2010, FE 200000 / FN 600000 / CM 127 / k=1.0)
  분석 격자는 EPSG:5179 (UTM-K, FE 1000000 / FN 2000000 / CM 127.5 / k=0.9996)
  → 반드시 재투영해야 한다. 둘 다 GRS80/KGD2002라 datum 변환은 없다.

입력은 읽기 전용. 원본 SHP는 건드리지 않는다.

출력  derived/cell_admin.parquet        prow, pcol, adm_cd, adm_nm
      derived/admin_dong.geojson        지도 오버레이용 경계 (EPSG:4326, 간소화)
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize

SHP     = r"C:\sb2\mask\BND_ADM_DONG_PG (2)\BND_ADM_DONG_PG.shp"
MASK    = r'V:\data\mask\common_mask_500m_5179.tif'
DERIVED = r'C:\for_sgis\data\grid_data\derived'
WEBDATA = r'C:\for_sgis\web\public\data'

os.makedirs(WEBDATA, exist_ok=True)

with rasterio.open(MASK) as s:
    mask_arr, transform = s.read(1), s.transform
    shape = (s.height, s.width)
valid_rows, valid_cols = np.where(mask_arr == 1)
print(f'격자 {shape}  유효 {len(valid_rows):,}셀')

g = gpd.read_file(SHP, encoding='cp949')
print(f'행정동 {len(g):,}개  원본 CRS {g.crs}')
g = g.to_crs(5179)
print(f'재투영 후 bounds: {[round(v) for v in g.total_bounds]}')

# 정수 인덱스로 래스터화 후 코드로 되돌린다 (문자열은 rasterize 불가)
g = g.reset_index(drop=True)
g['idx'] = np.arange(1, len(g) + 1, dtype=np.int32)

arr = rasterize(
    [(geom, i) for geom, i in zip(g.geometry, g['idx']) if geom is not None],
    out_shape=shape, transform=transform, fill=0, all_touched=False,
    dtype=np.int32,
)
hit = arr[valid_rows, valid_cols]
print(f'행정동 매칭: {int((hit > 0).sum()):,} / {len(valid_rows):,} '
      f'({100*(hit > 0).mean():.2f}%)')

# 미매칭 셀은 경계 밖(해안 등) — 최근접 행정동으로 보정
miss = np.where(hit == 0)[0]
if len(miss):
    print(f'미매칭 {len(miss):,}셀 → 최근접 행정동으로 보정')
    cen = g.geometry.representative_point()
    cx, cy = cen.x.values, cen.y.values
    mx = transform.c + 500 * (valid_cols[miss] + 0.5)
    my = transform.f - 500 * (valid_rows[miss] + 0.5)
    for k in range(0, len(miss), 20000):
        sl = slice(k, k + 20000)
        d2 = (mx[sl, None] - cx[None, :]) ** 2 + (my[sl, None] - cy[None, :]) ** 2
        hit[miss[sl]] = g['idx'].values[np.argmin(d2, axis=1)]

lut = pd.DataFrame({
    'prow': valid_rows.astype(np.int16), 'pcol': valid_cols.astype(np.int16),
    'idx': hit,
}).merge(g[['idx', 'ADM_CD', 'ADM_NM']], on='idx', how='left')
lut = lut.rename(columns={'ADM_CD': 'adm_cd', 'ADM_NM': 'adm_nm'}).drop(columns='idx')
lut.to_parquet(os.path.join(DERIVED, 'cell_admin.parquet'), index=False)

print(f'\n대응표 저장: {len(lut):,}행  고유 행정동 {lut["adm_cd"].nunique():,}개')
print('\n격자 수 상위 행정동:')
print(lut.groupby(['adm_cd', 'adm_nm']).size().nlargest(8).to_string())

# ── 지도 오버레이용 경계 (간소화 + 4326) ────────────────────────────
web = g[['ADM_CD', 'ADM_NM', 'geometry']].copy()
web['geometry'] = web.geometry.simplify(120)          # 500m 격자 서비스라 120m면 충분
web = web.to_crs(4326)
web = web.rename(columns={'ADM_CD': 'cd', 'ADM_NM': 'nm'})
out = os.path.join(WEBDATA, 'admin_dong.geojson')
web.to_file(out, driver='GeoJSON')
print(f'\n경계 GeoJSON: {out}  ({os.path.getsize(out)/1e6:.2f} MB)')

# 검증 — 앞서 역지오코딩으로 확인했던 지점과 대조
chk = [(1106715.73, 1805062.65, '의성 금성면'), (1158394.33, 1892748.27, '울진 북면')]
print('\n검증 (SGIS 지오코딩 좌표 → 대응표 조회):')
for x, y, label in chk:
    r = int((transform.f - y) // 500)
    c = int((x - transform.c) // 500)
    row = lut[(lut['prow'] == r) & (lut['pcol'] == c)]
    got = f"{row.iloc[0]['adm_nm']} ({row.iloc[0]['adm_cd']})" if len(row) else '격자 밖'
    print(f'  {label:<12} → {got}')
