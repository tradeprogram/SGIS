"""
SGIS 500m 격자경계 SHP → GRID_CD ↔ 좌표 대응표 생성.

격자통계 CSV는 헤더 없이 (연도, 격자ID, 항목코드, 값) 구조이고,
격자ID(예: 나나75b77b)는 500M 경계 SHP의 DBF 필드 GRID_CD와 같은 체계다.
따라서 CSV를 공간에 붙이려면 GRID_CD → 좌표 대응이 먼저 필요하다.

산출물: sgis_gridcd_500m.parquet  (GRID_CD, sgis_x, sgis_y)
  sgis_x/sgis_y = 셀 좌하단 좌표(EPSG:5179) — 26번 mask_to_sgis_500m.parquet과 같은 키

검증
  - 모든 셀이 500m 크기이고 500 배수에 정렬돼 있는지 (22b 결과와 일치해야 함)
  - 26번 대응표가 요구하는 셀 중 몇 개가 실제로 커버되는지
"""

import os, glob
import numpy as np
import pandas as pd
import shapefile   # pyshp

ROOT     = r'C:\for_sgis\data\grid_data'
LUT_PATH = r'C:\for_sgis\data\grid_data\derived\mask_to_sgis_500m.parquet'
OUT_PATH = r'C:\for_sgis\data\grid_data\derived\sgis_gridcd_500m.parquet'
CELL     = 500.0

shps = sorted(glob.glob(os.path.join(ROOT, '**', '*_500M.shp'), recursive=True))
print(f'500M 격자경계 SHP: {len(shps)}개')

frames = []
for p in shps:
    tile = os.path.basename(p).replace('grid_', '').replace('_500M.shp', '')
    # .cpg 선언이 UTF-8 (CP949 아님 — 국내 배포 shp의 통상 관행과 다르니 주의)
    sf = shapefile.Reader(p, encoding='utf-8', encodingErrors='replace')
    codes, xs, ys = [], [], []
    for rec, shp in zip(sf.records(), sf.shapes()):
        codes.append(rec[0])
        bb = shp.bbox           # (xmin, ymin, xmax, ymax)
        xs.append(bb[0]); ys.append(bb[1])
    sf.close()
    if not codes:
        print(f'  [빈 파일] {tile}')
        continue
    frames.append(pd.DataFrame({'GRID_CD': codes,
                                'sgis_x': np.round(xs).astype(np.int32),
                                'sgis_y': np.round(ys).astype(np.int32),
                                'tile': tile}))
    print(f'  {tile}: {len(codes):,}개')

grid = pd.concat(frames, ignore_index=True)
before = len(grid)
grid = grid.drop_duplicates('GRID_CD').reset_index(drop=True)
print(f'\n총 격자: {before:,} → 중복 제거 후 {len(grid):,}개')

# 검증 1: 500 배수 정렬 (22b에서 API로 확인한 내용을 실제 배포 파일로 재확인)
mx = np.unique(grid['sgis_x'].values % int(CELL))
my = np.unique(grid['sgis_y'].values % int(CELL))
print(f'x mod 500 고유값: {mx}   y mod 500 고유값: {my}')
print(f'→ 500 배수 정렬: {"예" if (len(mx)==1 and mx[0]==0 and len(my)==1 and my[0]==0) else "아니오"}')

# 검증 2: 마스크가 요구하는 셀 중 커버율
lut = pd.read_parquet(LUT_PATH)
need = lut[['sgis_x', 'sgis_y']].drop_duplicates()
have = grid[['sgis_x', 'sgis_y']].drop_duplicates()
merged = need.merge(have.assign(_ok=1), on=['sgis_x', 'sgis_y'], how='left')
n_need, n_ok = len(merged), int(merged['_ok'].notna().sum())
print(f'\n마스크가 필요로 하는 SGIS 셀: {n_need:,}개')
print(f'  경계 파일에 존재: {n_ok:,}개 ({100*n_ok/n_need:.2f}%)')
print(f'  누락: {n_need-n_ok:,}개')

# 누락 셀이 마스크 픽셀 가중치에서 차지하는 비중
miss = merged[merged['_ok'].isna()][['sgis_x', 'sgis_y']]
if len(miss):
    lost = lut.merge(miss.assign(_miss=1), on=['sgis_x', 'sgis_y'], how='inner')
    per_pix = lost.groupby(['prow', 'pcol'])['weight'].sum()
    print(f'  영향 픽셀: {len(per_pix):,}개 / 403,385  '
          f'(가중치 손실 평균 {per_pix.mean():.3f}, 최대 {per_pix.max():.3f})')
    print(f'  가중치 100% 손실(완전 결측) 픽셀: {int((per_pix > 0.999).sum()):,}개')

grid.to_parquet(OUT_PATH, index=False)
print(f'\n저장: {OUT_PATH}')
print(grid.head().to_string(index=False))
