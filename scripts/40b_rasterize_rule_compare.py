"""
폴리곤 → 500m 격자 변환 규칙 비교.

문제: all_touched=True 는 1ha짜리 dNBR 조각도 500m 격자(25ha)를 통째로 1로 만든다.
      47건 합산 시 폴리곤 실면적 19,122ha → 격자화 79,950ha (4.2배 팽창).
      양성 라벨의 3/4이 실제로는 타지 않은 셀이 되어 학습을 오염시킬 수 있다.

비교하는 규칙
  A. all_touched=True        스치기만 해도 1            (현재 40번 / README §5)
  B. all_touched=False       셀 중심이 폴리곤 안이면 1   (rasterio 기본)
  C. 면적비율 >= 0.25        셀의 25% 이상이 탔으면 1
  D. 면적비율 >= 0.50        셀의 절반 이상이 탔으면 1

면적비율은 500m 셀을 50m 격자(10×10=100개)로 세분해 피복률로 근사한다.
"""

import os, glob, re
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine

REF      = r'C:\for_sgis\data\fire_reference'
POLY_DIR = os.path.join(REF, 'burned_area_polygons_masked')
MASK     = r'V:\data\mask\common_mask_500m_5179.tif'
OUT      = r'C:\for_sgis\data\grid_data\derived\rasterize_rule_compare.csv'
SUB      = 10          # 500m → 50m 세분 (10×10)

with rasterio.open(MASK) as s:
    mask_arr = s.read(1)
    T = s.transform
    H, W = s.height, s.width

rows = []
for f in sorted(glob.glob(os.path.join(POLY_DIR, '*.gpkg'))):
    fid = int(re.match(r'fire_(\d+)_', os.path.basename(f)).group(1))
    g = gpd.read_file(f)
    if g.crs is not None and g.crs.to_epsg() != 5179:
        g = g.to_crs(5179)
    geoms = [x for x in g.geometry if x is not None and not x.is_empty]
    if not geoms:
        continue
    poly_ha = g.geometry.area.sum() / 10000

    # 폴리곤 bbox → 500m 셀 범위로 좁혀 세분 래스터화 (전국 세분은 메모리 과다)
    minx, miny, maxx, maxy = g.total_bounds
    c0 = max(0, int((minx - T.c) // 500) - 1)
    c1 = min(W, int((maxx - T.c) // 500) + 2)
    r0 = max(0, int((T.f - maxy) // 500) - 1)
    r1 = min(H, int((T.f - miny) // 500) + 2)
    nr, nc = r1 - r0, c1 - c0
    if nr <= 0 or nc <= 0:
        continue

    win_T = Affine(500, 0, T.c + 500 * c0, 0, -500, T.f - 500 * r0)
    sub_T = Affine(500 / SUB, 0, win_T.c, 0, -500 / SUB, win_T.f)

    at = rasterize([(x, 1) for x in geoms], out_shape=(nr, nc), transform=win_T,
                   fill=0, all_touched=True, dtype=np.uint8)
    ct = rasterize([(x, 1) for x in geoms], out_shape=(nr, nc), transform=win_T,
                   fill=0, all_touched=False, dtype=np.uint8)
    fine = rasterize([(x, 1) for x in geoms], out_shape=(nr * SUB, nc * SUB), transform=sub_T,
                     fill=0, all_touched=False, dtype=np.uint8)
    frac = fine.reshape(nr, SUB, nc, SUB).mean(axis=(1, 3))

    valid = mask_arr[r0:r1, c0:c1] == 1
    rows.append({
        'fire_id': fid, 'poly_ha': round(poly_ha, 1), 'n_poly': len(geoms),
        'A_all_touched': int(((at == 1) & valid).sum()),
        'B_center':      int(((ct == 1) & valid).sum()),
        'C_frac25':      int(((frac >= 0.25) & valid).sum()),
        'D_frac50':      int(((frac >= 0.50) & valid).sum()),
    })

d = pd.DataFrame(rows)
for k in ['A_all_touched', 'B_center', 'C_frac25', 'D_frac50']:
    d[k + '_ha'] = d[k] * 25
d.to_csv(OUT, index=False, encoding='utf-8-sig')

print(f'폴리곤 보유 {len(d)}건\n')
print('=== 규칙별 전체 합계 ===')
tot_poly = d['poly_ha'].sum()
print(f'  폴리곤 실면적                {tot_poly:>10,.0f} ha   (기준)')
for k, lab in [('A_all_touched', 'A. all_touched=True    '),
               ('B_center',      'B. 셀 중심 포함        '),
               ('C_frac25',      'C. 면적비율 >= 0.25    '),
               ('D_frac50',      'D. 면적비율 >= 0.50    ')]:
    ha = d[k + '_ha'].sum()
    n = d[k].sum()
    print(f'  {lab} {ha:>10,.0f} ha   셀 {n:>6,}개   팽창 {ha/tot_poly:>5.2f}배')

print('\n=== 셀이 0개가 되는 사건 수 (라벨 소실) ===')
for k in ['A_all_touched', 'B_center', 'C_frac25', 'D_frac50']:
    print(f'  {k:<16} {int((d[k] == 0).sum())}건')

print('\n=== 주요 사건별 셀 수 ===')
key = d.nlargest(12, 'poly_ha')[['fire_id', 'poly_ha', 'n_poly',
                                 'A_all_touched', 'B_center', 'C_frac25', 'D_frac50']]
print(key.to_string(index=False))
print(f'\n저장: {OUT}')
