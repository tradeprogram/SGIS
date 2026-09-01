"""
label_t1/t2/t3 양성 중 '신규 발화'가 실제로 몇 %인지 감사.

배경: model1(4v1) GRU의 라벨은 12b_build_multihorizon_labels_4v1.py에서
      label_tH = 1 iff 같은 픽셀이 t+H시에 label==1 집합에 있음
      으로 정의된다. label==1은 09_build_dataset_4v1.py에서 화재 발생시각~종료시각을
      1시간 단위로 확장한 것이므로, label_tH 양성 대부분이 '이미 타고 있던 픽셀이
      계속 타는' 경우일 수 있다(예전 t+6h 기록: 99.9%).

이 스크립트는 t+1/2/3h에서 그 비율을 실제로 측정한다.

  지표 A (느슨) : label_tH==1 인데 t 시점 label==0  → "지금은 불이 없는 픽셀"
  지표 B (엄격) : t+H 시각이 그 픽셀의 실제 발화시각(ignite_h)과 일치 → "진짜 신규발화"

출력: outputs/label_audit_new_ignition.csv  (horizon × 연도별 집계)
"""

import os
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol
import pyproj

NAS       = r'V:\data'
SEQ_PATH  = NAS + r'\ml_dataset\seq_dataset_12h_multih_4v1.parquet'
MASK_PATH = NAS + r'\mask\common_mask_500m_5179.tif'
GEO_CSV   = NAS + r'\wildfire_reference\fire_events_geocoded.csv'
OUT_DIR   = r'C:\for_sgis\outputs'
HORIZONS  = [1, 2, 3]

os.makedirs(OUT_DIR, exist_ok=True)

# ── 1. 시퀀스 데이터셋 로드 (라벨 관련 컬럼만) ─────────────────────────
cols = ['prow', 'pcol', 'year', 'month', 'day', 'hour', 'label',
        'label_t1', 'label_t2', 'label_t3']
df = pd.read_parquet(SEQ_PATH, columns=cols)
df['datetime'] = pd.to_datetime(df[['year', 'month', 'day', 'hour']])
df['prow'] = df['prow'].astype(int)
df['pcol'] = df['pcol'].astype(int)
print(f'시퀀스 데이터셋: {df.shape}')
print(f'  t 시점 label==1 (현재 화재중): {int(df["label"].sum()):,}개')
for H in HORIZONS:
    print(f'  label_t{H}==1: {int(df[f"label_t{H}"].sum()):,}개')

# ── 2. 실제 발화시각(ignite_h) 집합 재구성 ────────────────────────────
# 09_build_dataset_4v1.py와 동일한 좌표변환·필터를 사용해야 픽셀이 맞는다.
with rasterio.open(MASK_PATH) as src:
    transform = src.transform
    shape = (src.height, src.width)

geo = pd.read_csv(GEO_CSV, encoding='utf-8-sig')
geo['start_dt'] = pd.to_datetime(geo['datetime'])
geo = geo[
    (geo['start_dt'].dt.year.between(2021, 2025)) &
    (geo['start_dt'].dt.month.isin([2, 3, 4, 5, 6])) &
    geo['lon'].notna()
].copy()

transformer = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:5179', always_xy=True)
xs, ys = transformer.transform(geo['lon'].values, geo['lat'].values)
rs = np.array([rowcol(transform, x, y)[0] for x, y in zip(xs, ys)])
cs = np.array([rowcol(transform, x, y)[1] for x, y in zip(xs, ys)])
geo['prow'] = rs
geo['pcol'] = cs
geo = geo[(geo['prow'] >= 0) & (geo['prow'] < shape[0]) &
          (geo['pcol'] >= 0) & (geo['pcol'] < shape[1])].copy()
geo['ignite_h'] = geo['start_dt'].dt.floor('h')

ignite = geo[['prow', 'pcol', 'ignite_h']].drop_duplicates().reset_index(drop=True)
ignite['is_ignite'] = 1
print(f'\n실제 발화 시공간 포인트(픽셀×발화시각): {len(ignite):,}개 / 화재사건 {len(geo):,}건')

# ── 3. horizon별 감사 ────────────────────────────────────────────────
rows = []
for H in HORIZONS:
    pos = df[df[f'label_t{H}'] == 1].copy()
    pos['target_dt'] = pos['datetime'] + pd.Timedelta(hours=H)

    # 지표 A: t 시점에 불이 안 붙어 있었는가
    a_new = (pos['label'] == 0)

    # 지표 B: t+H가 그 픽셀의 실제 발화시각인가
    m = pos[['prow', 'pcol', 'target_dt']].merge(
        ignite.rename(columns={'ignite_h': 'target_dt'}),
        on=['prow', 'pcol', 'target_dt'], how='left')
    b_new = m['is_ignite'].fillna(0).astype(bool).values

    rows.append({
        'horizon': f't+{H}h',
        'year': 'ALL',
        'n_pos': len(pos),
        'n_notburning_at_t': int(a_new.sum()),
        'pct_notburning_at_t': round(100 * a_new.mean(), 2),
        'n_true_ignition': int(b_new.sum()),
        'pct_true_ignition': round(100 * b_new.mean(), 2),
    })

    pos = pos.assign(_a=a_new.values, _b=b_new)
    for yr, g in pos.groupby('year'):
        rows.append({
            'horizon': f't+{H}h',
            'year': int(yr),
            'n_pos': len(g),
            'n_notburning_at_t': int(g['_a'].sum()),
            'pct_notburning_at_t': round(100 * g['_a'].mean(), 2),
            'n_true_ignition': int(g['_b'].sum()),
            'pct_true_ignition': round(100 * g['_b'].mean(), 2),
        })

res = pd.DataFrame(rows)
out = os.path.join(OUT_DIR, 'label_audit_new_ignition.csv')
res.to_csv(out, index=False, encoding='utf-8-sig')

print('\n=== 결과 ===')
print(res.to_string(index=False))
print(f'\n저장: {out}')
