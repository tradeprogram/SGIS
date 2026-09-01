"""
대응 우선순위 최종본 — 33/33b를 하나로 통합.  (33, 33b는 탐색 기록으로 남겨둠)

확정된 규칙
  대상   WUI(산림-도시 경계) 격자
           forest_ratio = lc_conifer + lc_deciduous + lc_mixed_forest >= FOREST_MIN (0.3)
           pop_total >= POP_MIN (10)
  순위   규칙 B — 두 백분위의 평균
           haz_rank  = 위험 백분위 (100 = 전국 최고 위험)
           expo_rank = 노출인구 백분위 (100 = 전국 최다)
           score     = (haz_rank + expo_rank) / 2

왜 이 규칙인가
  - Hazard × 인구 같은 곱셈식은 단위가 다른 값을 곱하는 것이라 근거가 없다.
    두 값 모두 0~100 백분위라 평균은 단위 문제가 없고 가중치가 명시적이다.
  - "위험 상위 5% 안에서 인구순"(규칙 A)은 인구가 4자릿수로 벌어져 사실상 인구만으로
    순위가 정해지고 대도시가 독식했다. 규칙 B는 Top-20 평균 위험백분위를
    2.62% → 0.59%로 낮추면서 노출인구는 74,276 → 53,379명만 줄었다.
  - 가중치를 50:50에서 바꿔 민감도를 보이기 쉽다.

확률 표기 금지
  1:10 재표본화 학습이라 sigmoid 출력은 실제 발생확률이 아니다(전국 평균 0.13).
  전국 상대 백분위로만 표현한다.

산출물
  priority_final_{stamp}.parquet   전 격자 (WUI 여부·점수 포함)
  priority_final_top_{stamp}.csv   Top-N (지명 포함)
  priority_sensitivity_{stamp}.csv WUI 기준 민감도
"""

import os, time
import numpy as np
import pandas as pd
import rasterio
import requests

DERIVED = r'C:\for_sgis\data\grid_data\derived'
NAS     = r'V:\data'
MASK    = NAS + r'\mask\common_mask_500m_5179.tif'
ENV     = r'C:\for_sgis\.env'

STAMP      = os.environ.get('STAMP', '20250322_1200')
YEAR       = int(STAMP[:4])
FOREST_MIN = 0.3
POP_MIN    = 10.0
W_HAZ      = 0.5          # 규칙 B 가중치 (haz : expo = 0.5 : 0.5)
TOP_N      = 20
HORIZONS   = [1, 2, 3]

AUTH = 'https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json'
RGC  = 'https://sgisapi.mods.go.kr/OpenAPI3/addr/rgeocode.json'

t0 = time.time()

# ── 입력 ─────────────────────────────────────────────────────────────
haz = pd.read_parquet(os.path.join(DERIVED, f'hazard_ignition_{STAMP}.parquet'))
exp = pd.read_parquet(os.path.join(DERIVED, 'mask_exposure_500m.parquet'))
df = haz.merge(exp, on=['prow', 'pcol'], how='left')
print(f'Hazard {haz.shape} + Exposure {exp.shape} → {df.shape}')

with rasterio.open(MASK) as s:
    T = s.transform
df['x_5179'] = T.c + 500 * (df['pcol'] + 0.5)
df['y_5179'] = T.f - 500 * (df['prow'] + 0.5)

rows, cols = df['prow'].values, df['pcol'].values
forest = np.zeros(len(df), dtype=np.float32)
for lc in ['conifer', 'deciduous', 'mixed_forest']:
    p = NAS + rf'\landcover_raster\landcover_{lc}_ratio_{YEAR}.tif'
    with rasterio.open(p) as s:
        arr = s.read(1).astype(np.float32); nd = s.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    forest += np.nan_to_num(arr[rows, cols], nan=0.0)
    del arr
df['forest_ratio'] = forest

# ── 백분위 + 점수 ────────────────────────────────────────────────────
for H in HORIZONS:
    df[f'haz_rank_t{H}'] = df[f'y_prob_t{H}'].rank(pct=True, na_option='keep') * 100
    df[f'haz_top_t{H}']  = 100 - df[f'haz_rank_t{H}']
df['expo_rank'] = df['pop_total'].rank(pct=True, na_option='keep') * 100

df['is_wui'] = (df['forest_ratio'] >= FOREST_MIN) & (df['pop_total'] >= POP_MIN)
for H in HORIZONS:
    df[f'score_t{H}'] = W_HAZ * df[f'haz_rank_t{H}'] + (1 - W_HAZ) * df['expo_rank']
    df.loc[~df['is_wui'], f'score_t{H}'] = np.nan

print(f'WUI 격자 (산림>={FOREST_MIN}, 인구>={POP_MIN}): {int(df["is_wui"].sum()):,} / {len(df):,}')

df.to_parquet(os.path.join(DERIVED, f'priority_final_{STAMP}.parquet'), index=False)

# ── Top-N (T+1h) ─────────────────────────────────────────────────────
top = df.nlargest(TOP_N, 'score_t1').reset_index(drop=True)

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
top.to_csv(os.path.join(DERIVED, f'priority_final_top_{STAMP}.csv'),
           index=False, encoding='utf-8-sig')

print(f'\n{"="*84}')
print(f'대응 우선지역 Top-{TOP_N}   T+1h   |   WUI ∩ 규칙B(위험·노출 백분위 평균)')
print(f'{"="*84}')
v = top[['지명', 'score_t1', 'haz_top_t1', 'forest_ratio',
         'pop_total', 'households', 'houses', 'low_count_only']].copy()
v.columns = ['지명', '점수', '위험상위%', '산림비율', '노출인구', '가구', '주택', '저값치환만']
v.index = range(1, len(v) + 1)
print(v.round(2).to_string())
print(f'\nTop-{TOP_N} 노출인구 합계 {top["pop_total"].sum():,.0f}명  '
      f'평균 위험상위 {top["haz_top_t1"].mean():.2f}%  평균 산림비율 {top["forest_ratio"].mean():.2f}')

# ── 민감도: WUI 기준을 바꾸면 Top-20이 얼마나 흔들리는가 ─────────────
print(f'\n{"="*84}')
print('WUI 기준 민감도 — 기준선(산림>=0.3, 인구>=10) 대비 Top-20 유지율')
print(f'{"="*84}')
base_set = set(zip(top['prow'], top['pcol']))
sens = []
for fmin in [0.2, 0.3, 0.4, 0.5]:
    for pmin in [5.0, 10.0, 20.0]:
        m = (df['forest_ratio'] >= fmin) & (df['pop_total'] >= pmin)
        sc = W_HAZ * df['haz_rank_t1'] + (1 - W_HAZ) * df['expo_rank']
        sc = sc.where(m)
        t = df.assign(_s=sc).nlargest(TOP_N, '_s')
        s = set(zip(t['prow'], t['pcol']))
        sens.append({'forest_min': fmin, 'pop_min': pmin,
                     'wui_격자': int(m.sum()),
                     'top20_유지': len(s & base_set),
                     '평균_위험상위%': round(t['haz_top_t1'].mean(), 2),
                     '노출인구합': int(t['pop_total'].sum())})
sdf = pd.DataFrame(sens)
sdf.to_csv(os.path.join(DERIVED, f'priority_sensitivity_{STAMP}.csv'),
           index=False, encoding='utf-8-sig')
print(sdf.to_string(index=False))

print(f'\n저장 완료  ({(time.time()-t0)/60:.1f}분)')
