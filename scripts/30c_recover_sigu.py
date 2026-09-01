"""
30b에서 남은 32건 3차 복구 — 시군구가 '시 + 구' 형태인 경우.

30b에서 잘못 짚은 점: locgungu가 이미 두 토큰을 담고 있다.
  address_raw = '충북 청주 상당 미원 월용 산30'
  → locsi='충북', locgungu='청주 상당', locmenu='미원', locdong='월용'
  (30b는 locmenu를 구 이름으로 착각해 '청주 상당시 미원구'를 만들었다)

따라서 locgungu를 분해해 '{a}시 {b}구'로 만들고, 읍면동은 locmenu, 리는 locdong에서 찾는다.
공백 없이 붙은 표기('청주상당', '포항북', '용인처인')도 함께 처리한다.
"""

import os, json
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
g = pd.read_csv(GEO_CSV, encoding='utf-8-sig')
todo = rec[rec['recover_level'].isna()].merge(
    g[['fire_id', 'locsi', 'locgungu', 'locmenu', 'locdong']], on='fire_id', how='left')
print(f'3차 복구 대상: {len(todo)}건')

# 공백 제거 형태 → 정식 시군구명 역색인
nospace = {}
for sd, sggs in hier.items():
    for sgg in sggs:
        nospace.setdefault((sd, sgg.replace(' ', '')), sgg)

n_fixed = 0
for _, r in todo.iterrows():
    sido = SIDO_FULL.get(str(r['locsi']).strip())
    if not sido:
        continue
    gungu = str(r['locgungu']).strip() if pd.notna(r['locgungu']) else ''
    menu  = str(r['locmenu']).strip() if pd.notna(r['locmenu']) else ''
    dong  = str(r['locdong']).strip() if pd.notna(r['locdong']) else ''

    sgg_keys = []
    parts = gungu.split()
    if len(parts) == 2:
        sgg_keys.append(f'{parts[0]}시 {parts[1]}구')
    if len(parts) == 1 and gungu:
        # 붙여쓴 표기: '청주상당' → '청주시 상당구'
        hit = nospace.get((sido, gungu + '시구'))
        for cand_key, full in nospace.items():
            if cand_key[0] == sido and cand_key[1].replace('시', '').replace('구', '') == gungu:
                sgg_keys.append(full)
        if hit:
            sgg_keys.append(hit)
    sgg_keys = [k for k in dict.fromkeys(sgg_keys) if k in hier.get(sido, {})]

    cands = []
    for sgg in sgg_keys:
        emds = hier[sido][sgg]
        hits = [e for e in emds if e.startswith(menu)] if menu else []
        for emd in hits:
            if dong:
                cands.append((f'{sido} {sgg} {emd} {dong}리', 'ri'))
                cands.append((f'{sido} {sgg} {emd} {dong}동', 'dong'))
            cands.append((f'{sido} {sgg} {emd}', 'emd'))
        if not hits and menu:
            cands.append((f'{sido} {sgg} {menu}동', 'dong'))
        cands.append((f'{sido} {sgg}', 'sgg'))

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
print(f'\n3차 복구: +{n_fixed}건')
print(f'최종 복구: {n_ok:,}/{len(rec):,}건 ({100*n_ok/len(rec):.1f}%)')
print(f'정밀도별: {rec["recover_level"].value_counts().to_dict()}')
area = rec.loc[rec['recover_level'].notna(), 'damagearea'].sum()
print(f'복구 피해면적: {area:,.1f}ha / {rec["damagearea"].sum():,.1f}ha '
      f'({100*area/rec["damagearea"].sum():.2f}%)')
if n_ok < len(rec):
    print(f'\n최종 실패 {len(rec)-n_ok}건 (합계 {rec.loc[rec["recover_level"].isna(),"damagearea"].sum():.2f}ha):')
    print(rec[rec['recover_level'].isna()][['address_raw', 'damagearea']].to_string(index=False))
