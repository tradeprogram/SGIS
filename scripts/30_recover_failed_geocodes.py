"""
지오코딩 실패 산불 사건 417건을 SGIS 지오코딩 API로 복구.

문제
  fire_events_geocoded.csv의 산불시즌 사건 1,801건 중 417건(23.2%)이 geocode_status=fail이라
  lon/lat이 없고, 09/21번의 `lon.notna()` 필터에서 통째로 탈락했다.
  탈락분 피해면적이 19,633ha로 전체의 56.8%이며, 여기에는
    2022-03-04 경북 울진 북면 두천 (16,302ha)  — 2022 울진·삼척 대형산불
    2025-03-22 경북 의성 금성 청로 (기록 57ha) — 2025 의성 대형산불(역대 최대)
  이 포함된다. 즉 현재 모델은 역대 1·2위 산불을 학습·검증에서 본 적이 없다.

방법
  원본 CSV의 주소는 축약형(`경북 의성 금성 청로`)이라 SGIS 지오코딩이 받지 못한다.
  SGIS 단계별 주소조회(addr/stage.json)로 시도→시군구→읍면동 정식명 계층을 만든 뒤
  축약명을 접두 매칭해 정식 주소(`경상북도 의성군 금성면 청로리`)로 복원하고 지오코딩한다.

  SGIS 지오코딩은 좌표를 EPSG:5179로 직접 반환하므로 재투영이 불필요하다
  (기존 CSV의 lon/lat은 EPSG:4326이므로 컬럼을 구분해 저장한다).

산출물
  derived/fire_events_geocode_recovered.csv  — 복구된 사건 + 매칭 정밀도(level)
  derived/sgis_admin_hierarchy.json          — 행정구역 계층 캐시
"""

import os, json, time
import numpy as np
import pandas as pd
import requests

ENV      = r'C:\for_sgis\.env'
GEO_CSV  = r'V:\data\wildfire_reference\fire_events_geocoded.csv'
OUT_DIR  = r'C:\for_sgis\data\grid_data\derived'
HIER     = os.path.join(OUT_DIR, 'sgis_admin_hierarchy.json')
OUT_CSV  = os.path.join(OUT_DIR, 'fire_events_geocode_recovered.csv')

AUTH = 'https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json'
STG  = 'https://sgisapi.mods.go.kr/OpenAPI3/addr/stage.json'
GC   = 'https://sgisapi.mods.go.kr/OpenAPI3/addr/geocode.json'

SIDO_FULL = {
    '서울': '서울특별시', '부산': '부산광역시', '대구': '대구광역시', '인천': '인천광역시',
    '광주': '광주광역시', '대전': '대전광역시', '울산': '울산광역시', '세종': '세종특별자치시',
    '경기': '경기도', '강원': '강원특별자치도', '충북': '충청북도', '충남': '충청남도',
    '전북': '전북특별자치도', '전남': '전라남도', '경북': '경상북도', '경남': '경상남도',
    '제주': '제주특별자치도',
}

os.makedirs(OUT_DIR, exist_ok=True)

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
print('AccessToken 발급 완료')

# ── 1. 행정구역 계층 구축 (캐시) ─────────────────────────────────────
if os.path.exists(HIER):
    hier = json.load(open(HIER, encoding='utf-8'))
    print(f'계층 캐시 로드: 시도 {len(hier)}개')
else:
    hier = {}
    sido = sess.get(STG, params={'accessToken': tok}, timeout=60).json()['result']
    print(f'시도 {len(sido)}개 조회')
    for sd in sido:
        sgg_list = {}
        j = sess.get(STG, params={'accessToken': tok, 'cd': sd['cd']}, timeout=60).json()
        for sg in (j.get('result') or []):
            emd = []
            j2 = sess.get(STG, params={'accessToken': tok, 'cd': sg['cd']}, timeout=60).json()
            for e in (j2.get('result') or []):
                emd.append(e['addr_name'])
            sgg_list[sg['addr_name']] = emd
        hier[sd['addr_name']] = sgg_list
        print(f"  {sd['addr_name']}: 시군구 {len(sgg_list)}개")
    json.dump(hier, open(HIER, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'계층 저장: {HIER}')

# ── 2. 실패 사건 로드 ────────────────────────────────────────────────
g = pd.read_csv(GEO_CSV, encoding='utf-8-sig')
g['dt'] = pd.to_datetime(g['datetime'])
season = g[(g['dt'].dt.year.between(2021, 2025)) & (g['dt'].dt.month.isin([2, 3, 4, 5, 6]))]
fail = season[season['lon'].isna()].copy()
print(f'\n복구 대상: {len(fail):,}건')

# ── 3. 주소 정규화 + 지오코딩 ────────────────────────────────────────
recs = []
t0 = time.time()

for i, r in enumerate(fail.itertuples()):
    sido = SIDO_FULL.get(str(r.locsi).strip())
    sgg_map = hier.get(sido, {}) if sido else {}

    gungu = str(r.locgungu).strip() if pd.notna(r.locgungu) else ''
    menu  = str(r.locmenu).strip() if pd.notna(r.locmenu) else ''
    dong  = str(r.locdong).strip() if pd.notna(r.locdong) else ''

    # 시군구: 축약명을 접두로 갖는 정식명 (예: 의성 → 의성군, 북 → 북구)
    sgg_cands = [k for k in sgg_map if k.startswith(gungu)] if gungu else []
    # '포항 북' 처럼 시군구가 두 토큰인 경우: 시군구+읍면 결합도 시도
    sgg_cands += [k for k in sgg_map if k.startswith(gungu + menu)] if (gungu and menu) else []
    sgg_cands = sorted(set(sgg_cands), key=len)

    cands = []
    for sgg in sgg_cands:
        emds = sgg_map.get(sgg, [])
        emd_cands = [e for e in emds if e.startswith(menu)] if menu else []
        for emd in emd_cands:
            if dong:
                cands.append((f'{sido} {sgg} {emd} {dong}리', 'ri'))
                cands.append((f'{sido} {sgg} {emd} {dong}동', 'dong'))
            cands.append((f'{sido} {sgg} {emd}', 'emd'))
        if not emd_cands and dong:
            # 읍면 없이 구 단위 주소 (예: 광주 남 석정)
            cands.append((f'{sido} {sgg} {dong}동', 'dong'))
            cands.append((f'{sido} {sgg} {dong}리', 'ri'))
        cands.append((f'{sido} {sgg}', 'sgg'))

    hit = None
    for addr, lvl in cands[:12]:
        try:
            j = sess.get(GC, params={'accessToken': tok, 'address': addr}, timeout=30).json()
        except Exception:
            continue
        res = (j.get('result') or {}).get('resultdata') or []
        if j.get('errCd') == 0 and res:
            hit = (addr, lvl, float(res[0]['x']), float(res[0]['y']))
            break

    recs.append({
        'fire_id': r.fire_id, 'datetime': r.datetime, 'damagearea': r.damagearea,
        'firecause': r.firecause, 'address_raw': r.address_raw,
        'matched_address': hit[0] if hit else None,
        'recover_level':   hit[1] if hit else None,
        'x_5179': hit[2] if hit else np.nan,
        'y_5179': hit[3] if hit else np.nan,
    })

    if (i + 1) % 50 == 0:
        ok = sum(1 for x in recs if x['recover_level'])
        print(f'  [{i+1:,}/{len(fail):,}] 복구 {ok:,}건  ({(time.time()-t0)/60:.1f}분)')

out = pd.DataFrame(recs)
out.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')

n_ok = int(out['recover_level'].notna().sum())
print(f'\n{"="*60}')
print(f'복구 성공: {n_ok:,}/{len(out):,}건 ({100*n_ok/len(out):.1f}%)')
print(f'정밀도(level)별: {out["recover_level"].value_counts().to_dict()}')
rec_area = out.loc[out['recover_level'].notna(), 'damagearea'].sum()
print(f'복구된 피해면적: {rec_area:,.1f}ha / 대상 {out["damagearea"].sum():,.1f}ha '
      f'({100*rec_area/max(out["damagearea"].sum(),1):.1f}%)')
print(f'\n실패 잔여 {len(out)-n_ok:,}건 샘플:')
print(out[out['recover_level'].isna()][['address_raw']].head(10).to_string(index=False))
print(f'\n=== 주요 사건 복구 확인 ===')
key = out[out['address_raw'].astype(str).str.contains('울진|의성|홍성|금산', na=False)]
print(key[['datetime', 'address_raw', 'matched_address', 'recover_level',
           'x_5179', 'y_5179', 'damagearea']].to_string(index=False))
print(f'\n저장: {OUT_CSV}  ({(time.time()-t0)/60:.1f}분)')
