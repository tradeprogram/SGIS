"""
Stage2 모델 A/B — 실제 발화 사건 순위로 비교.

왜 AUROC 로 안 끝내는가
  검증셋 AUROC 는 순위 전체의 평균적 분리도다. 우리가 화면에 쓰는 건
  "우선대응 상위 1%" 라서 꼬리 4,034셀의 순서만 본다. 둘은 다른 것을 잰다.
  실제로 v4b CNN 은 AUROC 가 높은데 상위 1% 포착률은 낮았다.

비교 방법
  51번을 같은 스캔 시각(11·14시)으로 두 모델에 대해 돌린 뒤,
  발화 사건별 "최선 순위"(그 사건을 가장 높게 매긴 시각·horizon)를 맞대어 본다.
  스캔 시각이 다르면 비교가 성립하지 않으므로 반드시 통제한다.

사용
  python scripts/63_compare_stage2_ab.py            # 기본: GRU vs _cnn20
  AB_SUFFIX=_cnn20 python scripts/63_compare_stage2_ab.py
"""

import os
import glob

import numpy as np
import pandas as pd

ROOT   = os.path.join(r'C:', os.sep, 'for_sgis')
D      = os.path.join(ROOT, 'data', 'grid_data', 'derived', 'daily_scan')
OUT    = os.path.join(ROOT, 'outputs')
SUFFIX = os.environ.get('AB_SUFFIX', '_cnn20')
HOURS  = [int(h) for h in os.environ.get('SCAN_HOURS', '11,14').split(',')]
BASE_EXCLUDE = ('_h08', '_h10', SUFFIX)


def load(pattern, keep):
    fs = [f for f in sorted(glob.glob(os.path.join(D, pattern)))
          if keep(os.path.basename(f))]
    if not fs:
        raise SystemExit(f'파일 없음: {pattern}')
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True), fs


base, fb = load('ignition_ranks_20*.parquet',
                lambda b: not any(s in b for s in BASE_EXCLUDE))
alt, fa = load(f'ignition_ranks_*{SUFFIX}.parquet', lambda b: True)
print(f'기준(GRU) {len(fb)}개 파일 / 비교{SUFFIX} {len(fa)}개 파일')

# 스캔 시각 통제 — 기준 쪽은 추가 시각(_h08/_h10)을 제외해도 11·14시 외 행이 섞일 수 있다.
base = base[base['scan_hour'].isin(HOURS)]
alt = alt[alt['scan_hour'].isin(HOURS)]

key = ['fire_id', 'scan_hour', 'horizon']
m = base.merge(alt, on=key, suffixes=('_a', '_b'))
if len(m) == 0:
    raise SystemExit('겹치는 (사건, 시각, horizon) 이 없다. 스캔 시각을 확인하라.')

b = pd.DataFrame({
    'a': m.groupby('fire_id')['haz_top_pct_a'].min(),
    'b': m.groupby('fire_id')['haz_top_pct_b'].min(),
    'ha': m.groupby('fire_id')['damagearea_a'].first(),
    'yr': m.groupby('fire_id')['ignite_h_a'].first().dt.year,
})
print(f'매칭 사건 {len(b):,}건 (스캔시각 {HOURS} 통제)\n')

print(f'{"구간":<14}{"기준":>9}{"비교":>11}{"차이":>11}')
rows = []
for t in (1, 5, 10, 20):
    ga, gb = (b['a'] <= t).mean() * 100, (b['b'] <= t).mean() * 100
    print(f'상위 {t:>2}% 이내      {ga:>7.1f}%{gb:>9.1f}%{gb - ga:>+9.1f}%p')
    rows.append({'metric': f'top{t}pct', 'base': ga, 'alt': gb, 'diff': gb - ga})
print(f'{"중앙값":<14}   {b["a"].median():>7.1f}%{b["b"].median():>9.1f}%'
      f'{b["b"].median() - b["a"].median():>+9.1f}%p')
print(f'\n비교 개선 {(b["b"] < b["a"]).sum():,}건 / 악화 {(b["b"] > b["a"]).sum():,}건')

print('\n■ 연도별 상위10% 포착률')
print(f'{"연도":>6}{"n":>7}{"기준":>10}{"비교":>10}')
for y, g in b.groupby('yr'):
    print(f'{y:>6}{len(g):>7,}{(g["a"] <= 10).mean() * 100:>9.1f}%{(g["b"] <= 10).mean() * 100:>9.1f}%')

print('\n■ 피해면적별 중앙 순위')
print(f'{"구간":<14}{"n":>6}{"기준":>10}{"비교":>10}')
for lo, hi, nm in [(0, 1, '1ha 미만'), (1, 10, '1~10ha'),
                   (10, 100, '10~100ha'), (100, 1e9, '100ha 이상')]:
    s = b[(b['ha'] >= lo) & (b['ha'] < hi)]
    if len(s):
        print(f'{nm:<14}{len(s):>6,}{s["a"].median():>9.1f}%{s["b"].median():>9.1f}%')

dst = os.path.join(OUT, f'stage2_ab{SUFFIX}.csv')
b.to_csv(dst, encoding='utf-8-sig')
print(f'\n사건별 원자료 저장 {dst}')
