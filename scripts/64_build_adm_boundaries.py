"""
행정동 경계 웹 자산 — SGIS BND_ADM_DONG_PG 를 지도용 GeoJSON + 지역 인덱스로.

왜 필요한가
  지도에 위험도만 깔려 있으면 "여기가 어디지"를 알 수 없다. 행정경계를 그려야
  화면이 지명과 연결되고, 왼쪽에서 지역을 고르면 그 동을 정확히 짚어줄 수 있다.

시도·시군구 이름 복원
  이 shapefile 에는 동 이름밖에 없다(ADM_CD 8자리, ADM_NM). 30번이 SGIS API 로
  받아 둔 sgis_admin_hierarchy.json 은 이름 계층만 있고 코드가 없다.
  둘을 잇기 위해 ADM_CD 앞 5자리로 묶은 동 이름 집합을 계층의 시군구 동 목록과
  집합 단위로 맞춘다. 동 이름 하나씩 맞추면 '삼성동'처럼 여러 구에 있는 이름에서
  깨지지만, 15개 안팎의 집합끼리 맞추면 유일하게 정해진다.
  실제로 252/252 완전일치, 중복 0 이었다.

시도 코드도 SGIS 자체 체계(11,21,22…39)라 표준 행정표준코드와 다르다.
그래서 코드표를 박아 넣지 않고 집합 매칭 결과에서 이름을 가져온다.

출력
  web/public/data/adm_dong.geojson   동 경계 (ADM_CD, 이름, 시도/시군구)
  web/public/data/adm_index.json     시도 → 시군구 → [{cd, nm, center, bbox}]
"""

import io
import json
import os

import geopandas as gpd

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
SHP  = os.path.join(ROOT, 'data', 'ref', 'adm_dong', 'BND_ADM_DONG_PG.shp')
HIER = os.path.join(ROOT, 'data', 'grid_data', 'derived', 'sgis_admin_hierarchy.json')
WEB  = os.path.join(ROOT, 'web', 'public', 'data')

# 500m 격자 위에 겹쳐 그리는 용도라 100m 아래 디테일은 화면에서 의미가 없다.
TOL   = float(os.environ.get('SIMPLIFY_M', '80'))
NDIG  = 5      # 약 1m

os.makedirs(WEB, exist_ok=True)

g = gpd.read_file(SHP, encoding='cp949')
print(f'입력 {len(g):,}개 동 | CRS {g.crs}')

# ── 시도·시군구 이름 복원 ────────────────────────────────────────────
hier = json.load(io.open(HIER, encoding='utf-8'))
by_set = {}
for sido, sggs in hier.items():
    for sgg, dongs in sggs.items():
        by_set[frozenset(dongs)] = (sido, sgg)

g['sgg_cd'] = g['ADM_CD'].str[:5]
named, missing = {}, []
for cd, sub in g.groupby('sgg_cd'):
    hit = by_set.get(frozenset(sub['ADM_NM']))
    if hit is None:
        missing.append(cd)
    else:
        named[cd] = hit
if missing:
    raise SystemExit(f'시군구 이름 복원 실패 {len(missing)}개: {missing[:5]}\n'
                     '  → 계층 캐시와 경계 파일의 기준일자가 다를 수 있다')
g['sido_nm'] = g['sgg_cd'].map(lambda c: named[c][0])
g['sgg_nm'] = g['sgg_cd'].map(lambda c: named[c][1])
print(f'시도 {g["sido_nm"].nunique()}개 / 시군구 {g["sgg_cd"].nunique()}개 이름 복원 완료')

# ── 단순화 후 경위도 ─────────────────────────────────────────────────
# 투영좌표(m)에서 단순화해야 허용오차가 거리 단위로 해석된다.
g['geometry'] = g.geometry.simplify(TOL, preserve_topology=True)
g = g.to_crs(4326)
g['geometry'] = g.geometry.set_precision(10 ** -NDIG)
g = g[~g.geometry.is_empty & g.geometry.notna()]

# 인덱스용 대표점 — 폴리곤 안에 반드시 들어가는 점(중심이 밖으로 나가는 동이 있다)
pt = g.geometry.representative_point()
bounds = g.geometry.bounds

out = g[['ADM_CD', 'ADM_NM', 'sido_nm', 'sgg_nm', 'geometry']].rename(
    columns={'ADM_CD': 'cd', 'ADM_NM': 'nm', 'sido_nm': 'sido', 'sgg_nm': 'sgg'})
dst = os.path.join(WEB, 'adm_dong.geojson')
out.to_file(dst, driver='GeoJSON')
print(f'경계 저장 {dst}  ({os.path.getsize(dst)/1e6:.1f}MB, 단순화 {TOL:.0f}m)')

# ── 지역 인덱스 ──────────────────────────────────────────────────────
idx = {}
for i, r in enumerate(g.itertuples()):
    b = bounds.iloc[i]
    idx.setdefault(r.sido_nm, {}).setdefault(r.sgg_nm, []).append({
        'cd': r.ADM_CD, 'nm': r.ADM_NM,
        'c': [round(pt.iloc[i].x, 5), round(pt.iloc[i].y, 5)],
        'b': [round(b.minx, 5), round(b.miny, 5), round(b.maxx, 5), round(b.maxy, 5)],
    })
for sido in idx:
    for sgg in idx[sido]:
        idx[sido][sgg].sort(key=lambda d: d['nm'])

dst2 = os.path.join(WEB, 'adm_index.json')
with io.open(dst2, 'w', encoding='utf-8') as f:
    json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))
print(f'인덱스 저장 {dst2}  ({os.path.getsize(dst2)/1e3:.0f}KB, '
      f'시도 {len(idx)} / 동 {sum(len(v) for s in idx.values() for v in s.values()):,})')
