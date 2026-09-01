"""
우선순위 Top-N 을 산림-도시 경계(WUI)로 한정한 변형.

문제: 33번의 우선순위 Top-20이 전부 도심 동(부산진구 가야2동 등)으로 채워진다.
      모델이 틀린 것이 아니라, 국내 산불 원인 1위가 담뱃불실화·쓰레기소각이라
      신규발화가 사람 활동 근처에서 일어나기 때문이다.
      다만 산불 대응 우선지역 목록에 도심 한복판이 1위로 오르면 설득력이 떨어진다.

WUI(Wildland-Urban Interface) = 산림 연료와 사람·자산이 함께 있는 격자.
  산불이 사람에게 실제 피해를 주는 곳이 여기이며, 국제적으로 확립된 개념이라
  임의 기준을 새로 만드는 것보다 방어하기 쉽다.

정의 (투명하게 공개하고 민감도 확인 가능한 단순 기준)
  forest_ratio = lc_conifer + lc_deciduous + lc_mixed_forest
  WUI 격자 = forest_ratio >= FOREST_MIN  AND  pop_total >= POP_MIN
"""

import os
import numpy as np
import pandas as pd
import rasterio
import requests

DERIVED = r'C:\for_sgis\data\grid_data\derived'
NAS     = r'V:\data'
MASK    = NAS + r'\mask\common_mask_500m_5179.tif'
ENV     = r'C:\for_sgis\.env'
STAMP   = os.environ.get('STAMP', '20250322_1200')
YEAR    = int(STAMP[:4])

FOREST_MIN = float(os.environ.get('FOREST_MIN', '0.3'))
POP_MIN    = float(os.environ.get('POP_MIN', '10'))
HAZ_TIER   = 5.0
TOP_N      = 20

AUTH = 'https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json'
RGC  = 'https://sgisapi.mods.go.kr/OpenAPI3/addr/rgeocode.json'

pri = pd.read_parquet(os.path.join(DERIVED, f'priority_{STAMP}.parquet'))
print(f'우선순위 레이어: {pri.shape}')

with rasterio.open(MASK) as s:
    mask_arr = s.read(1)
rows, cols = pri['prow'].values, pri['pcol'].values

forest = np.zeros(len(pri), dtype=np.float32)
for lc in ['conifer', 'deciduous', 'mixed_forest']:
    p = NAS + rf'\landcover_raster\landcover_{lc}_ratio_{YEAR}.tif'
    with rasterio.open(p) as s:
        arr = s.read(1).astype(np.float32); nd = s.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    forest += np.nan_to_num(arr[rows, cols], nan=0.0)
    del arr
pri['forest_ratio'] = forest
print(f'산림비율 분포: 중앙값 {np.median(forest):.3f}, '
      f'>= {FOREST_MIN} 인 격자 {int((forest >= FOREST_MIN).sum()):,}개')

wui = pri[(pri['forest_ratio'] >= FOREST_MIN) & (pri['pop_total'] >= POP_MIN)].copy()
print(f'WUI 격자 (산림>={FOREST_MIN}, 인구>={POP_MIN}): {len(wui):,}개 / {len(pri):,}')

tier = wui[wui['haz_top_t1'] <= HAZ_TIER].copy()
print(f'  그중 위험 상위 {HAZ_TIER}%: {len(tier):,}개')
top = tier.nlargest(TOP_N, 'pop_total').reset_index(drop=True)

env = {}
for line in open(ENV, encoding='utf-8-sig'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()
sess = requests.Session()
tok = sess.get(AUTH, params={'consumer_key': env['SGIS_CONSUMER_KEY'],
                             'consumer_secret': env['SGIS_CONSUMER_SECRET']},
               timeout=30).json()['result']['accessToken']

names = []
for r in top.itertuples():
    try:
        j = sess.get(RGC, params={'accessToken': tok, 'x_coor': r.x_5179,
                                  'y_coor': r.y_5179, 'addr_type': '20'}, timeout=30).json()
        res = j.get('result') or []
        names.append(res[0]['full_addr'] if res else '(해당없음)')
    except Exception:
        names.append('(조회실패)')
top['지명'] = names

print(f'\n{"="*78}')
print(f'WUI 대응 우선지역 Top-{TOP_N}  —  T+1h, 위험 상위 {HAZ_TIER}% ∩ 산림>={FOREST_MIN} ∩ 인구>={POP_MIN}')
print(f'{"="*78}')
v = top[['지명', 'haz_top_t1', 'forest_ratio', 'pop_total', 'households', 'houses', 'low_count_only']].copy()
v.columns = ['지명', '위험상위%', '산림비율', '노출인구', '가구', '주택', '저값치환만']
v.index = range(1, len(v) + 1)
print(v.round(3).to_string())

print(f'\n노출인구 합계: {top["pop_total"].sum():,.0f}명')
print(f'평균 산림비율: {top["forest_ratio"].mean():.3f}')

top.to_csv(os.path.join(DERIVED, f'priority_wui_top_{STAMP}.csv'), index=False, encoding='utf-8-sig')
pri.to_parquet(os.path.join(DERIVED, f'priority_{STAMP}.parquet'), index=False)
print(f'\n저장: priority_wui_top_{STAMP}.csv  (forest_ratio 컬럼도 priority parquet에 추가)')
