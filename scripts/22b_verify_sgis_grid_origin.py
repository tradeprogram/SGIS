"""
SGIS 500m 격자 원점 검증 — 22번 면적가중치의 전제를 실측으로 확인.

22번은 "SGIS 500m 격자가 EPSG:5179에서 500의 배수에 정렬돼 있다"고 가정하고
공통 마스크 셀 → SGIS 셀 4개 배분 가중치를 계산했다.
격자통계 CSV 다운로드를 기다리지 않고, 행정구역 격자경계 OpenAPI로 실제
격자 폴리곤 좌표를 받아 이 가정을 검증한다.

  API: https://sgisapi.mods.go.kr/OpenAPI3/grid/data.geojson
       adm_cd 5자리(시군구) + grid_level_div=500m
"""

import os, json
import numpy as np
import requests

ENV_PATH = r'C:\for_sgis\.env'
AUTH_URL = 'https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json'
GRID_URL = 'https://sgisapi.mods.go.kr/OpenAPI3/grid/data.geojson'
CELL     = 500.0

# 검증 대상 시군구 — 지리적으로 떨어진 곳을 골라 전국 공통 원점인지 본다.
# adm_cd는 법정동코드가 아니라 SGIS 자체 코드다 (addr/stage.json으로 조회).
TARGETS = {
    '강원 강릉시': '32030',
    '경북 문경시': '37090',
    '서울 강남구': '11230',
}

env = {}
for line in open(ENV_PATH, encoding='utf-8-sig'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

r = requests.get(AUTH_URL, params={'consumer_key': env['SGIS_CONSUMER_KEY'],
                                   'consumer_secret': env['SGIS_CONSUMER_SECRET']}, timeout=30)
auth = r.json()
if auth.get('errCd') != 0:
    raise SystemExit(f'인증 실패: {auth}')
token = auth['result']['accessToken']
print(f'AccessToken 발급 완료 (만료 {auth["result"]["accessTimeout"]})')

for name, adm_cd in TARGETS.items():
    resp = requests.get(GRID_URL, params={'accessToken': token, 'adm_cd': adm_cd,
                                          'grid_level_div': '500m'}, timeout=120)
    try:
        gj = resp.json()
    except Exception:
        print(f'\n{name}: 응답 파싱 실패 (HTTP {resp.status_code}) {resp.text[:200]}')
        continue

    feats = gj.get('features') or []
    if not feats:
        print(f'\n{name}: features 없음 — errCd={gj.get("errCd")} errMsg={gj.get("errMsg")}')
        continue

    xs, ys = [], []
    for f in feats:
        geom = f.get('geometry') or {}
        for ring in (geom.get('coordinates') or []):
            for pt in ring:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    xs.append(float(pt[0])); ys.append(float(pt[1]))
    if not xs:
        print(f'\n{name}: 좌표 추출 실패')
        continue

    xs = np.array(xs); ys = np.array(ys)
    mx = np.unique(np.round(xs % CELL, 3))
    my = np.unique(np.round(ys % CELL, 3))
    print(f'\n{name}: 격자 {len(feats):,}개, 꼭짓점 {len(xs):,}개')
    print(f'  x 범위 {xs.min():.1f} ~ {xs.max():.1f}   x mod 500 = {mx[:5]}')
    print(f'  y 범위 {ys.min():.1f} ~ {ys.max():.1f}   y mod 500 = {my[:5]}')
    aligned = (len(mx) == 1 and abs(mx[0]) < 1e-3) and (len(my) == 1 and abs(my[0]) < 1e-3)
    print(f'  → 500 배수 정렬: {"예 (22번 가정 성립)" if aligned else "아니오 — 가중치 재계산 필요"}')

    # 셀 크기도 확인
    ux = np.unique(np.round(np.sort(np.unique(xs)), 3))
    if len(ux) > 1:
        d = np.diff(ux)
        print(f'  x 좌표 간격(고유): {np.unique(np.round(d, 3))[:5]}')
