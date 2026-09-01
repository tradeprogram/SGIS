"""
대응 우선순위(Priority) 레이어 — Hazard × Exposure.

FORECAST → EXPOSE → PRIORITIZE 의 마지막 단계.
  Hazard   : 32번 신규발화 GRU 전국 격자 예측 (t+1/2/3h)
  Exposure : 29번 SGIS 500m 인구·가구·주택 (면적가중 배분)
  Priority : 위험 상위 구간 안에서 노출 규모로 순위화

설계 원칙 — 임의의 곱셈식을 만들지 않는다
  Risk = Hazard × 인구 × ... 같은 식은 단위가 다른 값을 곱하는 것이라 근거가 없다.
  대신 두 축을 각각 백분위로 제시하고, "위험 상위 X% 안에서 노출이 큰 순서"로 정의한다.
  가중치가 없으므로 심사·검증 시 재현과 반박이 쉽다.

확률 표기 금지
  1:10 재표본화로 학습해 sigmoid 출력이 실제 발생확률이 아니다(전국 평균이 0.13에 달함).
  따라서 확률이 아니라 **전국 상대 백분위**로만 표현한다.

산출물
  priority_{stamp}.parquet   전 격자 hazard 백분위 + 노출 + 우선순위
  priority_top_{stamp}.csv   Top-N (지명 포함, SGIS 역지오코딩)
"""

import os, time
import numpy as np
import pandas as pd
import rasterio
import requests

DERIVED  = r'C:\for_sgis\data\grid_data\derived'
MASK     = r'V:\data\mask\common_mask_500m_5179.tif'
ENV      = r'C:\for_sgis\.env'
STAMP    = os.environ.get('STAMP', '20250322_1200')
HAZ_PATH = os.path.join(DERIVED, f'hazard_ignition_{STAMP}.parquet')
EXP_PATH = os.path.join(DERIVED, 'mask_exposure_500m.parquet')

HAZ_TIER = 5.0     # 위험 상위 몇 % 안에서 우선순위를 매길지
TOP_N    = 20
HORIZONS = [1, 2, 3]

AUTH = 'https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json'
RGC  = 'https://sgisapi.mods.go.kr/OpenAPI3/addr/rgeocode.json'

t0 = time.time()
haz = pd.read_parquet(HAZ_PATH)
exp = pd.read_parquet(EXP_PATH)
print(f'Hazard: {haz.shape}  Exposure: {exp.shape}')

df = haz.merge(exp, on=['prow', 'pcol'], how='left')
print(f'결합: {df.shape}  (노출 결측 {int(df["pop_total"].isna().sum()):,}행)')

# ── 위험 백분위 (전국, 유효 격자 기준) ───────────────────────────────
for H in HORIZONS:
    c = f'y_prob_t{H}'
    v = df[c]
    df[f'haz_pct_t{H}'] = v.rank(pct=True, na_option='keep') * 100    # 100 = 가장 위험
    df[f'haz_top_t{H}']  = 100 - df[f'haz_pct_t{H}']                  # 0 = 전국 1위

# ── 노출 백분위 ──────────────────────────────────────────────────────
df['expo_pct'] = df['pop_total'].rank(pct=True, na_option='keep') * 100

# ── 좌표 ─────────────────────────────────────────────────────────────
with rasterio.open(MASK) as s:
    T = s.transform
df['x_5179'] = T.c + 500 * (df['pcol'] + 0.5)
df['y_5179'] = T.f - 500 * (df['prow'] + 0.5)

out_cols = (['prow', 'pcol', 'x_5179', 'y_5179', 'P_lgbm']
            + [f'y_prob_t{H}' for H in HORIZONS]
            + [f'haz_top_t{H}' for H in HORIZONS]
            + ['pop_total', 'pop_male', 'pop_female', 'households', 'houses',
               'expo_pct', 'coverage', 'low_count_only'])
df[out_cols].to_parquet(os.path.join(DERIVED, f'priority_{STAMP}.parquet'), index=False)

# ── Top-N: 위험 상위 HAZ_TIER% 안에서 노출인구 순 ────────────────────
print(f'\n{"="*72}')
print(f'대응 우선지역 — 위험 상위 {HAZ_TIER}% 안에서 노출인구 순  (T+1h 기준)')
print(f'{"="*72}')

H = 1
tier = df[df[f'haz_top_t{H}'] <= HAZ_TIER].copy()
print(f'위험 상위 {HAZ_TIER}% 격자: {len(tier):,}개')
print(f'  그중 노출인구 0명: {int((tier["pop_total"] < 0.5).sum()):,}개 '
      f'({100*(tier["pop_total"] < 0.5).mean():.1f}%)')

top = tier.nlargest(TOP_N, 'pop_total').reset_index(drop=True)

# 비교용: 위험도만으로 뽑은 Top-N
haz_only = df.nsmallest(TOP_N, f'haz_top_t{H}').reset_index(drop=True)

# ── 지명 (SGIS 역지오코딩, Top-N 행만) ───────────────────────────────
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

place = {}
for d in (top, haz_only):
    for r in d.itertuples():
        key = (round(r.x_5179), round(r.y_5179))
        if key in place:
            continue
        try:
            j = sess.get(RGC, params={'accessToken': tok, 'x_coor': r.x_5179,
                                      'y_coor': r.y_5179, 'addr_type': '20'},
                         timeout=30).json()
            res = j.get('result') or []
            place[key] = res[0]['full_addr'] if res else '(해당없음)'
        except Exception:
            place[key] = '(조회실패)'

for d in (top, haz_only):
    d['지명'] = [place.get((round(r.x_5179), round(r.y_5179)), '') for r in d.itertuples()]

show = ['지명', 'haz_top_t1', 'pop_total', 'households', 'houses', 'low_count_only']
top_v = top[show].copy()
top_v.columns = ['지명', '위험상위%', '노출인구', '가구', '주택', '저값치환만']
top_v.index = range(1, len(top_v) + 1)
print(top_v.round(2).to_string())

print(f'\n{"-"*72}')
print(f'[비교] 위험도만으로 뽑은 Top-{TOP_N} — 노출을 보지 않으면')
print(f'{"-"*72}')
h_v = haz_only[show].copy()
h_v.columns = ['지명', '위험상위%', '노출인구', '가구', '주택', '저값치환만']
h_v.index = range(1, len(h_v) + 1)
print(h_v.round(2).to_string())

print(f'\n노출인구 합계  우선순위 Top-{TOP_N}: {top["pop_total"].sum():,.0f}명'
      f'   /   위험도만 Top-{TOP_N}: {haz_only["pop_total"].sum():,.0f}명')

top.to_csv(os.path.join(DERIVED, f'priority_top_{STAMP}.csv'), index=False, encoding='utf-8-sig')
haz_only.to_csv(os.path.join(DERIVED, f'hazardonly_top_{STAMP}.csv'), index=False, encoding='utf-8-sig')
print(f'\n저장 완료  ({(time.time()-t0)/60:.1f}분)')
