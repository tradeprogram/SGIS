"""
시간축 전국 위험등급 — "오늘은 5년 중 어느 정도로 위험한 날인가".

왜 필요한가
  지도에 쓰는 위험등급은 그날 하루 안에서의 공간 백분위다. 그래서 발화 0건인
  조용한 날도 상위 1%는 항상 빨갛게 칠해진다. 실제로 2025-05-19(발화 0건)의
  상위 1% 노출인구가 2025-03-22(발화 24건, 1,107ha)보다 많았다.
  공간 백분위만으로는 "오늘이 위험한 날인가"에 답할 수 없다.

무엇을 쓰는가
  51번이 시각별로 남긴 전국 집계 두 개.
    mean_prob  전국 403,385셀 평균 — 그 시각 전국이 얼마나 마르고 바람 부는가
    max_prob   전국 최고 셀       — 국지 극값. 단일 셀이라 튄다.
  기본 지표는 mean_prob 으로 한다.

먼저 확인해야 하는 것 — 연도 간 모델 오프셋
  누수 방지를 위해 연도마다 그 해를 학습에서 뺀 fold 모델을 쓴다. 즉 2021년 지도와
  2025년 지도는 서로 다른 모델이 그렸다. 다섯 모델의 출력 스케일이 어긋나 있으면
  5년 통합 백분위는 "그 해에 어느 모델이 배정됐는가"를 재는 지표가 되어버린다.

  그래서 등급을 매기기 전에 연도별 mean_prob 분포를 먼저 출력한다.
    - 연도 간 중앙값 차이가 작으면      → 5년 통합 분포로 백분위 (연도 비교 가능)
    - 크면                              → 연도별 표준화 필요. 다만 그러면 연도 간
                                          비교 의미가 사라지므로 UI 문구를 바꿔야 한다.
  판단은 숫자를 보고 내린다. 이 스크립트는 두 방식을 모두 계산해 나란히 보여준다.
"""

import os, json
import numpy as np
import pandas as pd

DERIVED = r'C:\for_sgis\data\grid_data\derived'
SRC     = os.path.join(DERIVED, 'daily_scan_all.csv')
OUT_JS  = r'C:\for_sgis\web\public\data\time_risk.json'

# 5년 통합 분포에서의 백분위 구간 → 4등급
LEVELS = [(90, '매우 높음'), (70, '높음'), (40, '주의'), (0, '보통')]

if not os.path.exists(SRC):
    raise SystemExit(f'51번 산출물이 없다: {SRC}\n  → 51_daily_scan_full_period.py 완료 후 실행')

d = pd.read_csv(SRC, encoding='utf-8-sig')
d['date'] = pd.to_datetime(d['date'])
d['year'] = d['date'].dt.year
print(f'입력 {len(d):,}행  {d["date"].min().date()} ~ {d["date"].max().date()}  '
      f'({d["date"].nunique():,}일 × {d["hour"].nunique()}시각)')

# ── 1. 연도 간 모델 오프셋 진단 ──────────────────────────────────────
print(f'\n{"="*70}\n연도별 mean_prob 분포 — fold 모델 간 스케일 정합성 확인\n{"="*70}')
g = d.groupby('year')['mean_prob'].agg(['count', 'mean', 'median', 'std',
                                        lambda s: s.quantile(.9)])
g.columns = ['n', 'mean', 'median', 'std', 'p90']
print(g.round(5).to_string())

med = g['median']
spread = float(med.max() / med.min()) if med.min() > 0 else np.inf
# 연도 내 변동(중앙값 대비 표준편차)과 연도 간 변동을 견준다
within = float((d.groupby('year')['mean_prob'].std() / med).mean())
between = float(med.std() / med.mean())
print(f'\n연도 간 중앙값 배율   {spread:.2f}배  (1.0 이면 완전 정합)')
print(f'연도 간 변동계수      {between:.3f}')
print(f'연도 내 변동계수 평균 {within:.3f}')

if between < within * 0.5:
    verdict = '통합'
    print('\n→ 연도 간 차이가 연도 내 변동보다 충분히 작다. 5년 통합 분포를 쓴다.\n'
          '   연도를 가로지르는 비교("2022-03-04는 5년 중 상위 X%")가 유효하다.')
else:
    verdict = '연도별'
    print('\n→ 연도 간 차이가 크다. fold 모델 스케일이 어긋나 있을 수 있다.\n'
          '   연도별 표준화 값을 기본으로 쓰고, UI 문구를 "그 해 안에서"로 바꿔야 한다.')

# ── 2. 두 방식 모두 계산 ─────────────────────────────────────────────
for col in ['mean_prob', 'max_prob']:
    d[f'{col}_pct_all']  = d[col].rank(pct=True) * 100
    d[f'{col}_pct_year'] = d.groupby('year')[col].rank(pct=True) * 100

base = 'mean_prob_pct_all' if verdict == '통합' else 'mean_prob_pct_year'
d['time_pct'] = d[base]
d['time_level'] = LEVELS[-1][1]
for thr, name in LEVELS:
    d.loc[d['time_pct'] >= thr, 'time_level'] = name

# ── 3. 검증 — 실제 대형산불 날이 상위로 나오는가 ─────────────────────
print(f'\n{"="*70}\n검증: 대형산불 발생일의 시간축 등급\n{"="*70}')
summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
summ['date'] = pd.to_datetime(summ['ignite_h']).dt.normalize()
day_ha = summ.groupby('date')['damagearea'].sum().rename('day_ha')
dd = d.groupby('date').agg(time_pct=('time_pct', 'max'),
                           lvl=('time_level', 'first')).join(day_ha).fillna({'day_ha': 0})

print('\n■ 피해면적 상위 10일')
print(dd.nlargest(10, 'day_ha')[['day_ha', 'time_pct', 'lvl']].round(1).to_string())
print('\n■ 시간축 위험도 상위 10일 — 이 날들에 실제로 불이 났는가')
print(dd.nlargest(10, 'time_pct')[['time_pct', 'day_ha', 'lvl']].round(1).to_string())

q = pd.qcut(dd['time_pct'], 4, labels=['하위25%', '25-50%', '50-75%', '상위25%'])
print('\n■ 시간축 등급 구간별 그날의 실제 피해면적')
print(dd.groupby(q, observed=True)['day_ha']
        .agg(['count', 'mean', 'median', 'max']).round(2).to_string())
corr = dd['time_pct'].corr(np.log1p(dd['day_ha']), method='spearman')
print(f'\n시간축 백분위 vs log(1+피해면적) 스피어만 상관: {corr:.3f}')

# ── 4. 웹 자산 저장 ──────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
rec = {}
for r in d.itertuples():
    rec.setdefault(str(r.date.date()), {})[int(r.hour)] = [
        round(float(r.time_pct), 1), r.time_level]
with open(OUT_JS, 'w', encoding='utf-8') as f:
    json.dump({'basis': verdict, 'levels': [n for _, n in LEVELS],
               'note': ('5년 전 기간 분포 대비' if verdict == '통합' else '해당 연도 분포 대비'),
               'days': rec}, f, ensure_ascii=False, separators=(',', ':'))
print(f'\n저장: {OUT_JS}  ({os.path.getsize(OUT_JS)/1024:.0f} KB, 기준={verdict})')
