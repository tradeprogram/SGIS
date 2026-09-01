"""
공통 마스크 유효픽셀 → SGIS 500m 격자 면적가중 대응표 생성.

22b에서 SGIS 격자가 EPSG:5179의 500 배수에 정렬됨을 실측 확인했으므로,
SGIS 셀은 좌하단 좌표 (sgis_x, sgis_y)로 유일하게 식별된다.
마스크 셀 하나가 SGIS 셀 4개에 걸치므로, 유효픽셀 403,385개 → 약 160만 행.

산출물: mask_to_sgis_500m.parquet
  prow, pcol       공통 마스크 픽셀 (기존 산불 피처와 동일 인덱스)
  sgis_x, sgis_y   SGIS 500m 셀 좌하단 좌표 (EPSG:5179)
  weight           면적 겹침 비율 (픽셀별 합 = 1.0)

사용법
  노출인구(마스크 셀) = Σ_over_4셀  SGIS인구(sgis_x, sgis_y) × weight
  → SGIS 셀 내부 인구 균등분포 가정. 보고서에 명시할 것.

격자통계 CSV 수령 후: CSV의 격자 식별자를 (sgis_x, sgis_y)로 변환해 이 표에 조인하면 끝.
"""

import os
import numpy as np
import pandas as pd
import rasterio

MASK_PATH = r'V:\data\mask\common_mask_500m_5179.tif'
OUT_PATH  = r'C:\for_sgis\data\grid_data\derived\mask_to_sgis_500m.parquet'
CELL      = 500.0

with rasterio.open(MASK_PATH) as s:
    mask_arr = s.read(1)
    t = s.transform
ox, oy = t.c, t.f

rows, cols = np.where(mask_arr == 1)
n = len(rows)
print(f'유효 픽셀: {n:,}개')

# 마스크 셀 경계 (EPSG:5179)
x0 = ox + CELL * cols                 # 좌
x1 = x0 + CELL                        # 우
y1 = oy - CELL * rows                 # 상
y0 = y1 - CELL                        # 하

# 겹치는 SGIS 셀의 좌하단 좌표: x는 서/동 2개, y는 남/북 2개
xw = np.floor(x0 / CELL) * CELL       # 서쪽 SGIS 열
xe = xw + CELL                        # 동쪽 SGIS 열
ys = np.floor(y0 / CELL) * CELL       # 남쪽 SGIS 행
yn = ys + CELL                        # 북쪽 SGIS 행

# 축별 겹침 길이 (클리핑으로 직접 계산 — 상수 가중치와 일치하는지 검증용)
ov_xw = np.clip(np.minimum(x1, xw + CELL) - np.maximum(x0, xw), 0, CELL)
ov_xe = np.clip(np.minimum(x1, xe + CELL) - np.maximum(x0, xe), 0, CELL)
ov_ys = np.clip(np.minimum(y1, ys + CELL) - np.maximum(y0, ys), 0, CELL)
ov_yn = np.clip(np.minimum(y1, yn + CELL) - np.maximum(y0, yn), 0, CELL)

print(f'축별 겹침 길이 (고유값):')
print(f'  x 서 {np.unique(np.round(ov_xw,3))}  x 동 {np.unique(np.round(ov_xe,3))}')
print(f'  y 남 {np.unique(np.round(ov_ys,3))}  y 북 {np.unique(np.round(ov_yn,3))}')

parts = []
for gx, ovx, xlab in [(xw, ov_xw, 'W'), (xe, ov_xe, 'E')]:
    for gy, ovy, ylab in [(yn, ov_yn, 'N'), (ys, ov_ys, 'S')]:
        w = (ovx * ovy) / (CELL * CELL)
        parts.append(pd.DataFrame({
            'prow': rows.astype(np.int32), 'pcol': cols.astype(np.int32),
            'sgis_x': gx.astype(np.int32), 'sgis_y': gy.astype(np.int32),
            'weight': w.astype(np.float32), 'quad': ylab + xlab,
        }))

lut = pd.concat(parts, ignore_index=True)
lut = lut[lut['weight'] > 0].reset_index(drop=True)

chk = lut.groupby(['prow', 'pcol'])['weight'].sum()
print(f'\n대응표: {len(lut):,}행  (픽셀당 평균 {len(lut)/n:.2f}개 SGIS 셀)')
print(f'픽셀별 가중치 합: 최소={chk.min():.6f}  최대={chk.max():.6f}  (1.0이어야 정상)')
print('\nquad별 가중치 (전 픽셀 동일해야 함):')
print(lut.groupby('quad')['weight'].agg(['mean', 'min', 'max', 'size']).round(6).to_string())

print(f'\n고유 SGIS 셀 수: {lut.groupby(["sgis_x","sgis_y"]).ngroups:,}개')

lut.to_parquet(OUT_PATH, index=False)
print(f'\n저장: {OUT_PATH}  ({os.path.getsize(OUT_PATH)/1e6:.1f} MB)')
