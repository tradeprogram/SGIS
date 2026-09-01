"""
시간축 전국 위험등급 — "오늘은 5년 중 어느 정도로 위험한 날인가".

왜 필요한가
  지도에 쓰는 위험등급은 그날 하루 안에서의 공간 백분위다. 그래서 발화 0건인
  조용한 날도 상위 1%는 항상 빨갛게 칠해진다. 실제로 2025-05-19(발화 0건)의
  상위 1% 노출인구가 2025-03-22(발화 24건, 1,107ha)보다 많았다.
  공간 백분위만으로는 "오늘이 위험한 날인가"에 답할 수 없다.

무엇을 쓰는가
  51번이 시각별로 남긴 전국 집계 두 개.
    mean_prob  전국 403,385셀 평균 — 오늘 전국이 얼마나 넓게 위험한가
    max_prob   전국 최고 셀       — 어딘가 한 곳이 얼마나 극단으로 치솟았는가

  처음에는 mean_prob 만 쓰고 max_prob 은 "단일 셀이라 튄다"고 배제했다.
  2021~2022년 300일로 확인해보니 그 판단이 틀렸다. 네 지표 전부에서 max_prob 이
  mean_prob 을 앞섰고, 두 백분위의 평균(combo)이 상관에서 가장 좋았다.

    지표        발화건수  log(1+총ha)  log(1+최대ha)  100ha+일 AUC
    mean_prob     0.777      0.742        0.722         0.830
    max_prob      0.784      0.770        0.749         0.869
    combo         0.806      0.779        0.758         0.860

  이유가 분명하다. 두 종류의 위험한 날은 서로 다른 지표가 잡는다.
    울진 2022-03-04 (16,302ha)  mean 85.0 / max 97.0
      양간지풍이 좁은 회랑에 몰린 사건이라 전국 평균은 평범했다.
    2021-02-21 (8건 336ha)      mean 99.3 / max 84.3
      전국이 고르게 말라 어디서든 날 수 있던 날이었다.

  그래서 기본 지표는 두 백분위의 평균으로 한다. 다만 100ha+ 판별 AUC 만은
  max_prob 이 앞서는데 그 표본이 8일뿐이라 결론을 내리기에 부족했다.
  아래에서 세 지표를 5년 전체로 다시 비교해 combo 채택이 유지되는지 확인한다.

먼저 확인해야 하는 것 — 연도 간 모델 오프셋
  누수 방지를 위해 연도마다 그 해를 학습에서 뺀 fold 모델을 쓴다. 즉 2021년 지도와
  2025년 지도는 서로 다른 모델이 그렸다. 다섯 모델의 출력 스케일이 어긋나 있으면
  5년 통합 백분위는 "그 해에 어느 모델이 배정됐는가"를 재는 지표가 되어버린다.

  연도 간 변동이 연도 내 변동의 절반 미만이면 통합 분포를 쓴다. 2021~2022 기준
  연도 간 0.265 / 연도 내 1.067 로 통과했다. 다만 2022년의 mean_prob 중앙값이
  2021년의 1.46배인 것은 모델 편차가 아니라 실제 기상 차이로 보인다 —
  2022년 발화 건수가 343건으로 2021년 158건의 2.17배였다. 방향이 일치한다.
  이 둘을 데이터만으로 완전히 분리할 수는 없다는 점은 한계로 남는다.

한계
  51번은 하루 중 11시·14시만 스캔한다. 강릉 옥계(2022-03-05 01시 발화)처럼
  심야에 시작된 산불은 이 지표가 제대로 잡지 못한다. 실제로 combo 70.7 에 그쳤다.
"""

import os, json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

DERIVED = r'C:\for_sgis\data\grid_data\derived'
SRC     = os.path.join(DERIVED, 'daily_scan_all.csv')
OUT_JS  = r'C:\for_sgis\web\public\data\time_risk.json'

LEVELS = [(90, '매우 높음'), (70, '높음'), (40, '주의'), (0, '보통')]

if not os.path.exists(SRC):
    raise SystemExit(f'51번 산출물이 없다: {SRC}\n  → 51_daily_scan_full_period.py 완료 후 실행')

d = pd.read_csv(SRC, encoding='utf-8-sig')
d['date'] = pd.to_datetime(d['date'])
d['year'] = d['date'].dt.year
print(f'입력 {len(d):,}행  {d["date"].min().date()} ~ {d["date"].max().date()}  '
      f'({d["date"].nunique():,}일 × {d["hour"].nunique()}시각)')

# ── 1. 연도 간 모델 오프셋 진단 ──────────────────────────────────────
print(f'\n{"="*70}\n연도별 mean_prob 분포 — fold 모델 간 스케일 정합성\n{"="*70}')
g = d.groupby('year')['mean_prob'].agg(['count', 'mean', 'median', 'std',
                                        lambda s: s.quantile(.9)])
g.columns = ['n', 'mean', 'median', 'std', 'p90']
print(g.round(5).to_string())

med = g['median']
within  = float((d.groupby('year')['mean_prob'].std() / med).mean())
between = float(med.std() / med.mean())
print(f'\n연도 간 중앙값 배율   {med.max() / med.min():.2f}배  (1.0 이면 완전 정합)')
print(f'연도 간 변동계수      {between:.3f}')
print(f'연도 내 변동계수 평균 {within:.3f}')

if between < within * 0.5:
    verdict = '통합'
    print('\n→ 연도 간 차이가 연도 내 변동보다 충분히 작다. 5년 통합 분포를 쓴다.\n'
          '   연도를 가로지르는 비교("2022-03-04는 5년 중 상위 X%")가 유효하다.')
else:
    verdict = '연도별'
    print('\n→ 연도 간 차이가 크다. fold 모델 스케일이 어긋나 있을 수 있다.\n'
          '   연도별 표준화 값을 기본으로 쓰고, UI 문구를 "그 해 안에서"로 바꾼다.')

# ── 2. 세 지표 × 두 기준 계산 ────────────────────────────────────────
for col in ['mean_prob', 'max_prob']:
    d[f'{col}_pct_all']  = d[col].rank(pct=True) * 100
    d[f'{col}_pct_year'] = d.groupby('year')[col].rank(pct=True) * 100
for sfx in ['all', 'year']:
    c = (d[f'mean_prob_pct_{sfx}'] + d[f'max_prob_pct_{sfx}']) / 2
    d[f'combo_pct_{sfx}'] = c.rank(pct=True) * 100

SFX = 'all' if verdict == '통합' else 'year'
d['time_pct'] = d[f'combo_pct_{SFX}']
d['time_level'] = LEVELS[-1][1]
for thr, name in LEVELS:
    d.loc[d['time_pct'] >= thr, 'time_level'] = name

# ── 3. 검증 ──────────────────────────────────────────────────────────
print(f'\n{"="*70}\n검증: 시간축 등급이 실제 발화를 예고하는가\n{"="*70}')
summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
summ['date'] = pd.to_datetime(summ['ignite_h']).dt.normalize()
agg = summ.groupby('date')['damagearea'].agg(day_ha='sum', day_n='count', day_max_ha='max')

dd = d.groupby('date').agg(time_pct=('time_pct', 'max'),
                           lvl=('time_level', 'first')).join(agg)
dd[['day_ha', 'day_n', 'day_max_ha']] = dd[['day_ha', 'day_n', 'day_max_ha']].fillna(0)

print('\n■ 피해면적 상위 10일')
print(dd.nlargest(10, 'day_ha')[['day_ha', 'day_n', 'time_pct', 'lvl']].round(1).to_string())
print('\n■ 시간축 위험도 상위 10일 — 이 날들에 실제로 불이 났는가')
print(dd.nlargest(10, 'time_pct')[['time_pct', 'day_n', 'day_ha', 'lvl']].round(1).to_string())

q = pd.qcut(dd['time_pct'], 4, labels=['하위25%', '25-50%', '50-75%', '상위25%'])
print('\n■ 시간축 구간별 실제 발화')
print(dd.groupby(q, observed=True)
        .agg(일수=('day_n', 'size'), 평균발화=('day_n', 'mean'),
             평균피해ha=('day_ha', 'mean'),
             무발화일=('day_n', lambda x: int((x == 0).sum()))).round(2).to_string())

print('\n■ 등급별')
print(dd.groupby('lvl', observed=True)
        .agg(일수=('day_n', 'size'), 평균발화=('day_n', 'mean'),
             평균피해ha=('day_ha', 'mean'),
             무발화일=('day_n', lambda x: int((x == 0).sum()))).round(2).to_string())

# ── 4. 지표 선택 재확인 (2년치로 고른 combo 가 5년에서도 최선인가) ───
print(f'\n{"="*70}\n지표 비교 — 5년 전체로 재확인\n{"="*70}')
big = (dd['day_max_ha'] >= 100).astype(int)
rows = []
for c in ['mean_prob', 'max_prob', 'combo']:
    v = d.groupby('date')[f'{c}_pct_{SFX}'].max().reindex(dd.index)
    r = {'지표': c,
         '발화건수':      v.corr(dd['day_n'], method='spearman'),
         'log(1+총ha)':   v.corr(np.log1p(dd['day_ha']), method='spearman'),
         'log(1+최대ha)': v.corr(np.log1p(dd['day_max_ha']), method='spearman')}
    if 1 < big.sum() < len(big):
        r['100ha+일 AUC'] = roc_auc_score(big, v)
    rows.append(r)
cmp = pd.DataFrame(rows).set_index('지표')
print(cmp.round(3).to_string())
print(f'\n100ha 이상 발생일 {int(big.sum())}일 / 전체 {len(dd)}일')

best = cmp['발화건수'].idxmax()
if best == 'combo':
    print('→ 2년치로 고른 combo 가 5년에서도 발화건수 상관 최고. 채택 유지.')
else:
    print(f'→ ⚠ 5년 기준으로는 {best} 가 더 낫다 (combo {cmp.loc["combo","발화건수"]:.3f} '
          f'vs {best} {cmp.loc[best,"발화건수"]:.3f}). 지표 재검토 필요.')

# ── 5. 웹 자산 저장 ──────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
rec = {}
for r in d.itertuples():
    rec.setdefault(str(r.date.date()), {})[int(r.hour)] = [
        round(float(r.time_pct), 1), r.time_level]
with open(OUT_JS, 'w', encoding='utf-8') as f:
    json.dump({'basis': verdict, 'metric': 'combo',
               'levels': [n for _, n in LEVELS],
               'note': ('5년 전 기간 분포 대비' if verdict == '통합' else '해당 연도 분포 대비'),
               'days': rec}, f, ensure_ascii=False, separators=(',', ':'))
print(f'\n저장: {OUT_JS}  ({os.path.getsize(OUT_JS)/1024:.0f} KB, 기준={verdict})')
