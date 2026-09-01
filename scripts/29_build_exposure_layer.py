"""
SGIS 500m 격자통계 → 공통 마스크 격자별 노출(Exposure) 레이어 생성.

경로
  격자통계 CSV (GRID_CD 단위)
    → 28번 sgis_gridcd_500m.parquet 로 GRID_CD → (sgis_x, sgis_y)
    → 26번 mask_to_sgis_500m.parquet 로 면적가중 배분
    → 마스크 픽셀(prow, pcol)별 노출값

주의사항 3가지
  1) CSV에 없는 격자 = 인구 0. (전국 합계 51,911,890명이 실제 인구와 일치하므로
     미수록 격자는 무인 지역으로 보는 것이 타당하다.)
     단, '경계 파일에도 없는 격자'는 값을 모르는 것이므로 0이 아니라 결측으로 처리하고
     해당 픽셀의 가중치를 남은 셀로 재정규화한다.
  2) 통계 비공개 처리: 1~4 값이 존재하지 않고 0/5/8에 몰려 있다(치환·반올림).
     저인구 격자의 개별 값은 오차가 크므로 품질 플래그를 함께 저장한다.
  3) 면적가중 배분은 SGIS 셀 내부 균등분포를 가정한다.

산출물: mask_exposure_500m.parquet
"""

import os, glob
import numpy as np
import pandas as pd

BASE     = r'C:\for_sgis\data\grid_data'
GRIDCD   = r'C:\for_sgis\data\grid_data\derived\sgis_gridcd_500m.parquet'
LUT      = r'C:\for_sgis\data\grid_data\derived\mask_to_sgis_500m.parquet'
OUT_PATH = r'C:\for_sgis\data\grid_data\derived\mask_exposure_500m.parquet'

SOURCES = {
    '_census_reqdoc_1788231611530': {'to_in_001': 'pop_total',
                                     'to_in_007': 'pop_male',
                                     'to_in_008': 'pop_female'},
    '_census_reqdoc_1788231661456': {'to_ga_001': 'households'},
    '_census_reqdoc_1788231714768': {'to_ho_001': 'houses'},
}
VALUE_COLS = ['pop_total', 'pop_male', 'pop_female', 'households', 'houses']

# ── 1. 격자통계 로드 → GRID_CD 단위 wide 테이블 ─────────────────────
stats = None
for folder, mapping in SOURCES.items():
    files = glob.glob(os.path.join(BASE, folder, '*.csv'))
    d = pd.concat([pd.read_csv(f, header=None, names=['year', 'GRID_CD', 'item', 'value'],
                               encoding='cp949', dtype={'GRID_CD': str, 'item': str})
                   for f in files], ignore_index=True)
    d = d[d['item'].isin(mapping)]
    d['value'] = pd.to_numeric(d['value'], errors='coerce')
    w = d.pivot_table(index='GRID_CD', columns='item', values='value', aggfunc='first')
    w = w.rename(columns=mapping)
    print(f'{folder}: {len(files)}개 파일 → 격자 {len(w):,}개, 컬럼 {list(w.columns)}')
    stats = w if stats is None else stats.join(w, how='outer')

stats = stats.reset_index()
print(f'\n격자통계 통합: {stats.shape}')
print(stats[VALUE_COLS].sum().round(0).to_string())

# ── 2. GRID_CD → 좌표 ────────────────────────────────────────────────
gcd = pd.read_parquet(GRIDCD)
stats = stats.merge(gcd[['GRID_CD', 'sgis_x', 'sgis_y']], on='GRID_CD', how='left')
n_nocoord = int(stats['sgis_x'].isna().sum())
print(f'\n좌표 매칭 실패 격자: {n_nocoord:,}개'
      f' (인구 {stats.loc[stats["sgis_x"].isna(), "pop_total"].sum():,.0f}명)')
stats = stats.dropna(subset=['sgis_x']).copy()
stats['sgis_x'] = stats['sgis_x'].astype(np.int32)
stats['sgis_y'] = stats['sgis_y'].astype(np.int32)

# ── 3. 마스크 대응표에 결합 ──────────────────────────────────────────
lut = pd.read_parquet(LUT)
known = gcd[['sgis_x', 'sgis_y']].drop_duplicates().assign(_known=True)
lut = lut.merge(known, on=['sgis_x', 'sgis_y'], how='left')
lut['_known'] = lut['_known'].fillna(False)

# 경계에 없는 셀은 값을 알 수 없음 → 제외 후 가중치 재정규화
lut_k = lut[lut['_known']].copy()
wsum = lut_k.groupby(['prow', 'pcol'])['weight'].transform('sum')
lut_k['weight_norm'] = lut_k['weight'] / wsum

cov = lut.groupby(['prow', 'pcol'])['weight'].sum().rename('w_all')
cov_k = lut_k.groupby(['prow', 'pcol'])['weight'].sum().rename('w_known')
qual = pd.concat([cov, cov_k], axis=1).fillna(0.0)
qual['coverage'] = qual['w_known'] / qual['w_all']
print(f'\n픽셀 커버리지: 평균 {qual["coverage"].mean():.4f}, '
      f'1.0 미만 {int((qual["coverage"] < 0.999).sum()):,}개, '
      f'0.5 미만 {int((qual["coverage"] < 0.5).sum()):,}개')

# ── 4. 면적가중 배분 ─────────────────────────────────────────────────
m = lut_k.merge(stats[['sgis_x', 'sgis_y'] + VALUE_COLS], on=['sgis_x', 'sgis_y'], how='left')
# 경계에 있으나 통계 CSV에 없는 격자 = 무인 → 0
m[VALUE_COLS] = m[VALUE_COLS].fillna(0.0)
for c in VALUE_COLS:
    m[c] = m[c] * m['weight_norm']

exp = m.groupby(['prow', 'pcol'])[VALUE_COLS].sum().reset_index()
exp = exp.merge(qual[['coverage']].reset_index(), on=['prow', 'pcol'], how='left')

# ── 5. 품질 플래그 ───────────────────────────────────────────────────
# 기여 격자가 전부 저인구 치환구간(0/5/8)이면 값 신뢰도가 낮다
low = stats[VALUE_COLS[:1] + ['sgis_x', 'sgis_y']].copy()
low['_low'] = low['pop_total'].isin([0, 5, 8])
m2 = lut_k.merge(low[['sgis_x', 'sgis_y', '_low']], on=['sgis_x', 'sgis_y'], how='left')
m2['_low'] = m2['_low'].fillna(True)          # CSV에 없는 무인 격자도 저값 취급
flag = m2.groupby(['prow', 'pcol'])['_low'].all().rename('low_count_only').reset_index()
exp = exp.merge(flag, on=['prow', 'pcol'], how='left')

exp.to_parquet(OUT_PATH, index=False)

print(f'\n저장: {OUT_PATH}')
print(f'shape: {exp.shape}')
print('\n노출 합계 (배분 후 — 원본 총합과 비교):')
for c in VALUE_COLS:
    print(f'  {c:<12} {exp[c].sum():>14,.0f}   (원본 {stats[c].sum():>14,.0f})')
print('\n픽셀별 총인구 분포:')
print(exp['pop_total'].describe(percentiles=[.5, .75, .9, .99]).round(2).to_string())
print(f'\n저인구 치환구간만으로 구성된 픽셀: {int(exp["low_count_only"].sum()):,} '
      f'({100*exp["low_count_only"].mean():.1f}%)')
