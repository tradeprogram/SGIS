"""
전 기간 타임라인 인덱스 — 741일을 하나의 날짜축에 세운다.

왜 만드는가
  사례 25일만 지도로 보여주면 "골라낸 사례"로 읽힌다. 51번은 이미 741일 전부에
  대해 전국 40만 격자를 추론했고, 이번에 그 결과를 일별 PNG 로도 구웠다.
  이 스크립트는 그 741일을 UI 가 한 번에 읽을 수 있는 작은 인덱스로 만든다.

무엇을 담는가 (10시 산출 = t+1h 11시 위험도 기준)
  등급/백분위   54번의 시간축 등급 — 5년 741일 중 오늘이 어디쯤인가
  노출          상위 1%·5% 격자의 SGIS 인구, WUI ∩ 상위 5% 격자 수
  실제 발화     그날 실제로 난 산불 건수·피해면적·지명
  사례일 여부   시간대별 상세 자산이 있는 25일인지

왜 10시인가
  51번이 스캔한 네 시각(8·10·11·14시) 중 10시의 t+1h 예측 대상인 11시가
  실제 발화가 가장 몰리는 시간대다 (145건 18,034ha, 단일 최대).
  "매일 오전 10시 산출 → 11시 위험도"라는 운영 서술과도 맞는다.

담지 못하는 것
  대응 우선지역 Top 10 은 셀 단위 위험도가 있어야 하는데, 51번은 집계만 남기고
  격자를 버린다(741일치를 다 남기면 수십 GB). 그래서 우선지역 목록은 격자를
  통째로 저장하는 사례 25일에만 있다. 전 기간 날짜에서는 전국 요약까지만 보여준다.
"""

import os, json
import numpy as np
import pandas as pd
import rasterio
import pyproj

DERIVED = r'C:\for_sgis\data\grid_data\derived'
WEB     = r'C:\for_sgis\web\public\data'
PNG_DIR = os.path.join(WEB, 'daily')
BASE_HH = 10

d = pd.read_csv(os.path.join(DERIVED, 'daily_scan_all.csv'), encoding='utf-8-sig')
d = d[d['hour'] == BASE_HH].copy()
d['date'] = pd.to_datetime(d['date'])
print(f'일별 스캔 {len(d):,}일  {d["date"].min().date()} ~ {d["date"].max().date()}')

# 시간축 등급
with open(os.path.join(WEB, 'time_risk.json'), encoding='utf-8') as f:
    tr = json.load(f)
lv = {k: v.get(str(BASE_HH)) for k, v in tr['days'].items()}

# 그날 실제 발화 — 지도에 찍으려면 좌표가 필요하다.
# 예측 지도와 실제 발화점을 겹쳐 봐야 "맞았나"를 눈으로 확인할 수 있다.
MASK = r'V:\data\mask\common_mask_500m_5179.tif'
with rasterio.open(MASK) as src:
    ox, oy = src.transform.c, src.transform.f
xform = pyproj.Transformer.from_crs('EPSG:5179', 'EPSG:4326', always_xy=True)

fc = pd.read_parquet(os.path.join(DERIVED, 'fire_cells.parquet'))
fc['ignite_h'] = pd.to_datetime(fc['ignite_h'])
fc = fc.drop_duplicates('fire_id')

summ = pd.read_csv(os.path.join(DERIVED, 'fire_cell_summary.csv'), encoding='utf-8-sig')
summ['date'] = pd.to_datetime(summ['ignite_h']).dt.normalize()
summ['hh'] = pd.to_datetime(summ['ignite_h']).dt.hour
summ = summ.merge(fc[['fire_id', 'prow', 'pcol']], on='fire_id', how='left')
ok = summ['prow'].notna()
lo = np.full(len(summ), np.nan)
la = np.full(len(summ), np.nan)
lo[ok.values], la[ok.values] = xform.transform(
    (ox + 500 * (summ.loc[ok, 'pcol'] + 0.5)).values,
    (oy - 500 * (summ.loc[ok, 'prow'] + 0.5)).values)
summ['lon'], summ['lat'] = lo, la
print(f'발화점 좌표 {int(ok.sum()):,}/{len(summ):,}건')

fires = {}
for dt_, grp in summ.groupby('date'):
    g = grp[grp['lon'].notna()].nlargest(40, 'damagearea')
    fires[dt_.strftime('%Y-%m-%d')] = {
        'n': int(len(grp)),
        'ha': round(float(grp['damagearea'].sum()), 1),
        'top': [[str(r.loc) if pd.notna(r.loc) else '', int(r.hh),
                 round(float(r.damagearea), 1),
                 round(float(r.lon), 5), round(float(r.lat), 5)]
                for r in g.itertuples()],
    }

# 시간대별 상세 자산이 있는 사례일
case = set()
if os.path.exists(os.path.join(WEB, 'days.json')):
    with open(os.path.join(WEB, 'days.json'), encoding='utf-8') as f:
        case = {x['date'] for x in json.load(f)['days']}
print(f'사례일(시간대별 상세) {len(case)}일')

rows, missing_png = [], 0
for r in d.itertuples():
    ds = r.date.strftime('%Y-%m-%d')
    ymd = r.date.strftime('%Y%m%d')
    if not os.path.exists(os.path.join(PNG_DIR, f'{ymd}_{BASE_HH:02d}.png')):
        missing_png += 1
        continue
    t = lv.get(ds)
    f = fires.get(ds)
    rows.append({
        'd': ds,
        'p': round(float(t[0]), 1) if t else None,   # 시간축 백분위
        'l': t[1] if t else None,                    # 등급
        'e1': int(r.top1_pop),                       # 상위 1% 노출인구
        'e5': int(r.top5_pop),
        'w': int(r.wui_top5_cells),
        'n': f['n'] if f else 0,
        'ha': f['ha'] if f else 0,
        'ft': f['top'] if f else [],
        'c': 1 if ds in case else 0,
    })
    if missing_png:
        pass

out = {
    'hour': BASE_HH,
    'note': f'매일 {BASE_HH:02d}시 산출 · t+1h({BASE_HH + 1:02d}시) 신규발화 위험도',
    'basis': tr.get('basis'),
    'levels': tr.get('levels'),
    'days': rows,
}
p = os.path.join(WEB, 'timeline.json')
with open(p, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

print(f'PNG 없어 제외 {missing_png}일')
print(f'저장: {p}  ({os.path.getsize(p)/1024:.0f} KB, {len(rows):,}일)')

t = pd.DataFrame(rows)
print('\n■ 등급 분포')
print(t['l'].value_counts().to_string())
print('\n■ 연도별')
t['y'] = pd.to_datetime(t['d']).dt.year
print(t.groupby('y').agg(일수=('d', 'size'), 사례일=('c', 'sum'),
                         발화일=('n', lambda s: int((s > 0).sum())),
                         총발화=('n', 'sum'), 총피해ha=('ha', 'sum')).to_string())
print(f'\n일별 PNG 용량 {sum(os.path.getsize(os.path.join(PNG_DIR, x)) for x in os.listdir(PNG_DIR))/1e6:.1f} MB')
