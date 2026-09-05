"""
전 기간 행정동 집계 → 챗봇 공간질의용 웹 자산.

왜 필요한가
  전 기간 737일 모드는 격자 단위 값을 저장하지 않는다(수십 GB). 그래서 챗봇이
  "강원도는 오늘 어떤가"에 답할 수 없었다. 51번이 REGION_AGG=1 로 남긴 행정동
  집계를 지역 계층으로 접어 웹에 올린다.

무엇을 담는가
  sido  17개 전부 — 조용한 날에도 "상위 5%에 든 격자가 없다"고 답하려면 필요하다
  sgg   그날 상위 5%에 든 격자가 있는 시군구만 — 전부 담으면 대부분 0이라 낭비다
  top   그날 가장 위험한 행정동 20개

  best 는 그 지역에서 가장 위험한 격자의 전국 상위 %다. 평균이 아니라 최솟값을
  쓰는 이유는 대응 판단이 "이 지역에 위험한 지점이 있는가"이지 "지역 전체가
  고르게 위험한가"가 아니기 때문이다. 소수 첫째 자리까지만 쓰므로 x10 정수로 줄인다.

출력  web/public/data/region_daily.json
"""

import glob
import io
import json
import os

import pandas as pd

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
SCAN = os.path.join(ROOT, 'data', 'grid_data', 'derived', 'daily_scan')
IDX = os.path.join(ROOT, 'web', 'public', 'data', 'adm_index.json')
OUT = os.path.join(ROOT, 'web', 'public', 'data', 'region_daily.json')

SUFFIX = os.environ.get('OUT_SUFFIX', '_h10')
BASE_HH = int(os.environ.get('BASE_HH', '10'))
TOP_DONG = 20

files = sorted(glob.glob(os.path.join(SCAN, f'region_scan_*{SUFFIX}.parquet')))
if not files:
    raise SystemExit(f'행정동 집계 없음: region_scan_*{SUFFIX}.parquet\n'
                     '  → 51번을 REGION_AGG=1 로 다시 돌려야 한다')
d = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
d = d[d['hour'] == BASE_HH]
d['date'] = d['date'].astype(str)
print(f'입력 {len(files)}개 파일 / {len(d):,}행 / {d["date"].nunique()}일')

# 동 코드 → 시도·시군구 (64번이 만든 인덱스)
hier = json.load(io.open(IDX, encoding='utf-8'))
cd2sgg, cd2sido, cd2nm = {}, {}, {}
for sido, sggs in hier.items():
    for sgg, dongs in sggs.items():
        for x in dongs:
            cd2sgg[x['cd']] = f'{sido} {sgg}'
            cd2sido[x['cd']] = sido
            cd2nm[x['cd']] = x['nm']

d['sgg'] = d['adm_cd'].map(cd2sgg)
d['sido'] = d['adm_cd'].map(cd2sido)
d['nm'] = d['adm_cd'].map(cd2nm)
miss = d['sgg'].isna().mean()
print(f'지역 매칭 실패 {miss:.2%}')
d = d.dropna(subset=['sgg'])

sidos = sorted(d['sido'].unique())
sggs = sorted(d['sgg'].unique())
si = {v: i for i, v in enumerate(sidos)}
gi = {v: i for i, v in enumerate(sggs)}
print(f'시도 {len(sidos)} / 시군구 {len(sggs)}')


def fold(g, key, idx, only_hit):
    a = g.groupby(key).agg(best=('best', 'min'), n1=('n1', 'sum'), n5=('n5', 'sum'),
                           pd_=('pd_', 'sum'), po=('po', 'sum')).reset_index()
    if only_hit:
        a = a[a['n5'] > 0]
    return [[idx[r[key]], int(round(r['best'] * 10)), int(r['n1']), int(r['n5']),
             int(round(r['pd_'])), int(round(r['po']))] for _, r in a.iterrows()]


days = {}
for date, g in d.groupby('date'):
    t = g.nsmallest(TOP_DONG, 'best')
    days[date] = {
        'sido': fold(g, 'sido', si, False),
        'sgg': fold(g, 'sgg', gi, True),
        'top': [[r['nm'], cd2sgg[r['adm_cd']].split(' ')[-1], int(round(r['best'] * 10))]
                for _, r in t.iterrows()],
    }

out = {
    'hour': BASE_HH,
    'note': 'best 는 x10 정수. 그 지역에서 가장 위험한 격자의 전국 상위 %.',
    'fields': ['idx', 'best_x10', 'n_top1', 'n_top5', 'pop_day', 'pop_old'],
    'sidos': sidos, 'sggs': sggs, 'days': days,
}
with io.open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
print(f'\n저장 {OUT}  ({os.path.getsize(OUT) / 1e6:.2f} MB, {len(days)}일)')

k = max(days, key=lambda x: -len(days[x]['sgg']))
print(f'\n예시 — 상위5% 시군구가 가장 많은 날 {k}')
for nm, sg, b in days[k]['top'][:5]:
    print(f'  {sg} {nm}  상위 {b / 10:.1f}%')
