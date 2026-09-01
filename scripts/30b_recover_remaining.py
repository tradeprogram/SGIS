"""
30번에서 남은 38건 2차 복구.

실패 패턴 두 가지
  1) 시군구가 두 토큰(시 + 구): `경남 창원 마산합포 구산 수정`
     → locgungu='창원', locmenu='마산합포' 로 잘려 SGIS의 '창원시 마산합포구'와 매칭 실패.
       locmenu를 구 이름으로 보고 '{gungu}시 {menu}구'를 시도하며, 읍면동은 locdong에서 찾는다.
  2) 관할 시도 변경: `경북 군위` → 2023년 대구광역시 군위군으로 편입.
     → 명시된 시도에서 못 찾으면 전국 시군구에서 이름으로 재탐색한다.
"""

import os, json
import numpy as np
import pandas as pd
import requests

ENV     = r'C:\for_sgis\.env'
GEO_CSV = r'V:\data\wildfire_reference\fire_events_geocoded.csv'
OUT_DIR = r'C:\for_sgis\data\grid_data\derived'
HIER    = os.path.join(OUT_DIR, 'sgis_admin_hierarchy.json')
REC_CSV = os.path.join(OUT_DIR, 'fire_events_geocode_recovered.csv')

AUTH = 'https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json'
GC   = 'https://sgisapi.mods.go.kr/OpenAPI3/addr/geocode.json'

SIDO_FULL = {
    '서울': '서울특별시', '부산': '부산광역시', '대구': '대구광역시', '인천': '인천광역시',
    '광주': '광주광역시', '대전': '대전광역시', '울산': '울산광역시', '세종': '세종특별자치시',
    '경기': '경기도', '강원': '강원특별자치도', '충북': '충청북도', '충남': '충청남도',
    '전북': '전북특별자치도', '전남': '전라남도', '경북': '경상북도', '경남': '경상남도',
    '제주': '제주특별자치도',
}

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

hier = json.load(open(HIER, encoding='utf-8'))
rec = pd.read_csv(REC_CSV, encoding='utf-8-sig')
todo = rec[rec['recover_level'].isna()].copy()
print(f'2차 복구 대상: {len(todo)}건')

g = pd.read_csv(GEO_CSV, encoding='utf-8-sig')
cols = ['fire_id', 'locsi', 'locgungu', 'locmenu', 'locdong']
todo = todo.merge(g[cols], on='fire_id', how='left')

# 전국 시군구 역색인 (시도 변경 대응)
allsgg = {}
for sd, sggs in hier.items():
    for sgg in sggs:
        allsgg.setdefault(sgg, []).append(sd)

n_fixed = 0
for idx, r in todo.iterrows():
    sido = SIDO_FULL.get(str(r['locsi']).strip())
    gungu = str(r['locgungu']).strip() if pd.notna(r['locgungu']) else ''
    menu  = str(r['locmenu']).strip() if pd.notna(r['locmenu']) else ''
    dong  = str(r['locdong']).strip() if pd.notna(r['locdong']) else ''

    pairs = []   # (시도, 시군구, 읍면동후보소스)

    # 패턴 1: 시군구 = '{gungu}시 {menu}구' → 읍면동은 locdong에서
    if sido and gungu and menu:
        key = f'{gungu}시 {menu}구'
        if key in hier.get(sido, {}):
            pairs.append((sido, key, dong))

    # 패턴 2: 다른 시도로 편입된 시군구 (예: 군위군 → 대구광역시)
    if gungu:
        for cand in (f'{gungu}군', f'{gungu}시', f'{gungu}구'):
            for sd in allsgg.get(cand, []):
                if sd != sido:
                    pairs.append((sd, cand, menu or dong))

    cands = []
    for sd, sgg, emd_src in pairs:
        emds = hier[sd][sgg]
        hits = [e for e in emds if e.startswith(emd_src)] if emd_src else []
        for emd in hits:
            tail = dong if emd_src == menu else ''
            if tail:
                cands.append((f'{sd} {sgg} {emd} {tail}리', 'ri'))
                cands.append((f'{sd} {sgg} {emd} {tail}동', 'dong'))
            cands.append((f'{sd} {sgg} {emd}', 'emd'))
        cands.append((f'{sd} {sgg}', 'sgg'))

    for addr, lvl in cands[:14]:
        try:
            j = sess.get(GC, params={'accessToken': tok, 'address': addr}, timeout=30).json()
        except Exception:
            continue
        res = (j.get('result') or {}).get('resultdata') or []
        if j.get('errCd') == 0 and res:
            m = rec['fire_id'] == r['fire_id']
            rec.loc[m, 'matched_address'] = addr
            rec.loc[m, 'recover_level']   = lvl
            rec.loc[m, 'x_5179'] = float(res[0]['x'])
            rec.loc[m, 'y_5179'] = float(res[0]['y'])
            n_fixed += 1
            print(f"  [복구] {r['address_raw']}  →  {addr}  ({lvl})")
            break

rec.to_csv(REC_CSV, index=False, encoding='utf-8-sig')

n_ok = int(rec['recover_level'].notna().sum())
print(f'\n2차 복구: +{n_fixed}건')
print(f'최종 복구: {n_ok:,}/{len(rec):,}건 ({100*n_ok/len(rec):.1f}%)')
print(f'정밀도별: {rec["recover_level"].value_counts().to_dict()}')
area = rec.loc[rec['recover_level'].notna(), 'damagearea'].sum()
print(f'복구 피해면적: {area:,.1f}ha / {rec["damagearea"].sum():,.1f}ha '
      f'({100*area/rec["damagearea"].sum():.1f}%)')
if n_ok < len(rec):
    print(f'\n최종 실패 {len(rec)-n_ok}건:')
    print(rec[rec['recover_level'].isna()][['address_raw', 'damagearea']].to_string(index=False))
