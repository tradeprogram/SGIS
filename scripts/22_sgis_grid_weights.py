"""
공통 분석 마스크(common_mask_500m_5179.tif) ↔ SGIS 500m 격자 정합 가중치 계산.

전제: 기존 산불 피처 래스터가 전부 이 마스크에 클립·정렬돼 있으므로 마스크가 분석 격자다.
      따라서 SGIS 격자통계를 마스크 격자로 끌어온다(반대 아님).

두 격자는 EPSG:5179·500m로 같지만 원점이 어긋나 있어 마스크 셀 하나가 SGIS 셀 4개에 걸친다.
어긋난 양(offset)이 전 격자에서 동일하므로 4셀 면적가중치는 상수 → 한 번 계산해 전국 적용.

가정: SGIS 500m 격자 경계가 EPSG:5179에서 500의 배수에 정렬돼 있다.
      → 격자경계 SHP(자료제공) 수령 후 22b에서 실측 검증할 것.
"""

import json
import rasterio

MASK_PATH = r'V:\data\mask\common_mask_500m_5179.tif'
OUT_PATH  = r'C:\for_sgis\data\grid_data\derived\sgis_grid_weights.json'
CELL      = 500.0

with rasterio.open(MASK_PATH) as s:
    t   = s.transform
    crs = s.crs
    w, h = s.width, s.height

ox, oy = t.c, t.f
print(f'마스크: {w} x {h}  CRS={crs}  픽셀={t.a} x {abs(t.e)}m')
print(f'원점: x={ox:.4f}  y={oy:.4f}')

dx = ox % CELL
dy = oy % CELL
print(f'\nSGIS 500m 격자선 대비 오프셋: x={dx:.4f}m  y={dy:.4f}m')

if abs(dx) < 1e-6 and abs(dy) < 1e-6:
    print('→ 두 격자가 정렬됨. 1:1 조인 가능.')
    weights = {'aligned': True}
else:
    # 마스크 셀 x범위 [dx, dx+500] 이 500 배수 격자선을 dx 지점에서 가른다.
    wx = [(CELL - dx) / CELL, dx / CELL]   # [서쪽 SGIS 열, 동쪽 SGIS 열]
    wy = [(CELL - dy) / CELL, dy / CELL]   # [북쪽 SGIS 행, 남쪽 SGIS 행]
    quad = {
        'NW': wy[0] * wx[0],
        'NE': wy[0] * wx[1],
        'SW': wy[1] * wx[0],
        'SE': wy[1] * wx[1],
    }
    print(f'열 가중치: 서 {wx[0]:.5f} / 동 {wx[1]:.5f}')
    print(f'행 가중치: 북 {wy[0]:.5f} / 남 {wy[1]:.5f}')
    print('\n마스크 셀 1개 → SGIS 셀 4개 면적가중치:')
    for k, v in quad.items():
        print(f'  {k}: {v:.5f}  ({v*100:.2f}%)')
    print(f'  합계: {sum(quad.values()):.6f}')
    weights = {'aligned': False, 'offset_x_m': dx, 'offset_y_m': dy,
               'w_col': wx, 'w_row': wy, 'quad': quad}

weights.update({'mask_origin_x': ox, 'mask_origin_y': oy,
                'cell_size_m': CELL, 'crs': str(crs),
                'mask_width': w, 'mask_height': h,
                'assumption': 'SGIS 500m 격자가 EPSG:5179에서 500 배수에 정렬됨 — SHP로 검증 필요'})

with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump(weights, f, ensure_ascii=False, indent=2)
print(f'\n저장: {OUT_PATH}')
