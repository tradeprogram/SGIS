"""
Tier 2 사례일 선정 — 규칙을 코드로 고정하고 결과를 공개한다.

왜 규칙을 먼저 박아두는가
  "좋은 날만 골라 보여준다"는 반박을 막으려면 선정 기준이 사후에 조정되지 않아야 한다.
  이 스크립트는 모델이나 예측 결과를 전혀 보지 않는다. 실제 산불 기록만 보고 고른다.

규칙
  연도별 피해면적 합계 상위 3일    = 15일   대형산불 대응 능력
  연도별 무작위 2일 (seed=42)      = 10일   평상시 작동 — 발화가 없는 날도 그대로 포함
  ────────────────────────────────────────
  합계 25일

  무작위 표본은 상위 3일을 제외한 나머지 산불시즌 전체(발화 없는 날 포함)에서 뽑는다.
  seed를 고정했으므로 누구나 같은 날짜를 재현할 수 있다.

출력  derived/case_days.json   선정 결과 + 규칙 기록
      derived/case_days.csv    사람이 보기 위한 표
"""

import os, json, calendar
import numpy as np
import pandas as pd

DERIVED = r'C:\for_sgis\data\grid_data\derived'
SEED    = 42
TOP_N_PER_YEAR    = 3
RANDOM_N_PER_YEAR = 2
YEARS   = [2021, 2022, 2023, 2024, 2025]
MONTHS  = [2, 3, 4, 5, 6]
DATA_CAP_2025 = pd.Timestamp('2025-06-26')

# ── 산불시즌 전체 일자 ───────────────────────────────────────────────
all_days = []
for y in YEARS:
    for m in MONTHS:
        for d in range(1, calendar.monthrange(y, m)[1] + 1):
            t = pd.Timestamp(y, m, d)
            if y == 2025 and t > DATA_CAP_2025:
                continue
            all_days.append(t)
all_days = pd.Series(all_days)
print(f'산불시즌 전체 {len(all_days)}일')

# ── 실제 산불 기록만 본다 (예측 결과는 보지 않는다) ──────────────────
s = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
s['ignite_h'] = pd.to_datetime(s['ignite_h'])
s['day'] = s['ignite_h'].dt.normalize()

daily = s.groupby('day').agg(
    n_fire=('fire_id', 'size'), ha=('damagearea', 'sum'), cells=('n_cells', 'sum')
).reindex(all_days.values, fill_value=0)
daily.index.name = 'day'
daily = daily.reset_index()
daily['year'] = daily['day'].dt.year
print(f'발화가 있던 날 {int((daily["n_fire"] > 0).sum())}일 / 없던 날 {int((daily["n_fire"] == 0).sum())}일')

# ── 선정 ─────────────────────────────────────────────────────────────
rng = np.random.default_rng(SEED)
picked = []

for y in YEARS:
    sub = daily[daily['year'] == y].copy()

    top = sub.nlargest(TOP_N_PER_YEAR, 'ha')
    for r in top.itertuples():
        picked.append({'date': r.day.strftime('%Y-%m-%d'), 'year': y, 'reason': 'top_damage',
                       'n_fire': int(r.n_fire), 'ha': round(float(r.ha), 2), 'cells': int(r.cells)})

    rest = sub[~sub['day'].isin(top['day'])].sort_values('day').reset_index(drop=True)
    idx = rng.choice(len(rest), size=min(RANDOM_N_PER_YEAR, len(rest)), replace=False)
    for i in sorted(idx):
        r = rest.iloc[i]
        picked.append({'date': r['day'].strftime('%Y-%m-%d'), 'year': y, 'reason': 'random',
                       'n_fire': int(r['n_fire']), 'ha': round(float(r['ha']), 2),
                       'cells': int(r['cells'])})

picked = sorted(picked, key=lambda x: x['date'])
out = {
    'rule': {
        'top_damage_per_year': TOP_N_PER_YEAR,
        'random_per_year': RANDOM_N_PER_YEAR,
        'seed': SEED,
        'note': '무작위 표본은 상위 3일을 제외한 산불시즌 전체(발화 없는 날 포함)에서 뽑는다. '
                '모델 예측 결과는 선정에 사용하지 않는다.',
    },
    'days': picked,
}
with open(os.path.join(DERIVED, 'case_days.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
df = pd.DataFrame(picked)
df.to_csv(os.path.join(DERIVED, 'case_days.csv'), index=False, encoding='utf-8-sig')

print(f'\n선정 {len(picked)}일 (연도별 상위{TOP_N_PER_YEAR} + 무작위{RANDOM_N_PER_YEAR}, seed={SEED})')
print(df.to_string(index=False))
print(f'\n무작위로 뽑힌 날 중 발화 0건: {int(((df["reason"] == "random") & (df["n_fire"] == 0)).sum())}일')
print(f'피해면적 합계: 상위선정 {df.loc[df.reason == "top_damage", "ha"].sum():,.0f}ha / '
      f'무작위 {df.loc[df.reason == "random", "ha"].sum():,.1f}ha')
