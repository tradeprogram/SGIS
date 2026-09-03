"""
v4b CNN 5-fold 로드·성능 검증.

60번에서 구조(Conv1d padding=1)를 fold5 v4 공간추론 결과와 대조해 6.17e-07 오차로
확정했다. v4b 는 가중치 shape 이 같으므로 같은 구조를 쓴다. 여기서는 다섯 fold 가
모두 정상 로드되고, 각자의 테스트 연도에서 교수님 CSV 와 비슷한 성능이 나오는지 본다.

주의 — 완전 일치는 기대할 수 없다
  교수님 CSV 의 n_test 는 1:10 재표본화된 부분집합이다(예: fold5 26,556행).
  재표본화 시드를 모르므로 같은 부분집합을 만들 수 없다. 여기서는 해당 연도
  전체 행으로 평가한다. 양성률이 달라지므로 AUPRC 는 크게 다르고, AUROC 는
  표본에 덜 민감해 비슷하게 나와야 한다. AUROC 가 크게 어긋나면 로드가 잘못된 것이다.
"""

import os, pickle
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT = r'C:\for_sgis'
SEQ  = os.path.join(ROOT, r'data\ml_dataset\seq_dataset_12h_multih_v2_allratios.parquet')
MDL  = os.path.join(ROOT, 'models_v4')
REF  = os.path.join(ROOT, r'outputs\v4\dl_cnn_v4b_allfold_results.csv')

LOOKBACK = 12
SEQ_COLS = []
for lag in range(LOOKBACK - 1, 0, -1):
    SEQ_COLS += [f'vpd_tm{lag}', f'wind_tm{lag}']
SEQ_COLS += ['vpd_t0', 'wind_t0']
STATIC = ['P_lgbm_r20', 'ndvi', 'ndmi', 'hum4d', 'prcp4d', 'doy_sin', 'doy_cos']
LABELS = ['label_t1', 'label_t2', 'label_t3']
YEARS  = [2021, 2022, 2023, 2024, 2025]


def build():
    """60번에서 확정한 구조. padding=1 이 아니면 값이 조용히 달라진다."""
    body = nn.Sequential(
        nn.Conv1d(2, 32, 3, padding=1), nn.ReLU(), nn.Dropout(0.0),
        nn.Conv1d(32, 64, 3, padding=1), nn.ReLU(), nn.AdaptiveMaxPool1d(1),
    )
    head = nn.Sequential(
        nn.Linear(64 + len(STATIC), 64), nn.ReLU(), nn.Dropout(0.0),
        nn.Linear(64, 3), nn.Sigmoid(),
    )
    return body, head


ref = pd.read_csv(REF)
d = pd.read_parquet(SEQ, columns=['year'] + SEQ_COLS + STATIC + LABELS)
print(f'입력 {len(d):,}행\n')
print(f'{"fold":>4} {"연도":>6} {"n":>9} {"양성률":>8} | '
      f'{"AUROC(재현)":>12} {"AUROC(CSV)":>11} {"차이":>8} | {"AUPRC(재현)":>12}')
print('-' * 84)

rows = []
for i, ty in enumerate(YEARS):
    tag = f'cnn_v4b_r20_fold{i+1}_test{ty}'
    with open(os.path.join(MDL, f'{tag}_scaler.pkl'), 'rb') as f:
        scaler = pickle.load(f)
    body, head = build()
    body.load_state_dict(torch.load(os.path.join(MDL, f'{tag}_body.pt'), map_location='cpu'))
    head.load_state_dict(torch.load(os.path.join(MDL, f'{tag}_head.pt'), map_location='cpu'))
    body.eval(); head.eval()

    sub = d[d['year'] == ty]
    X = sub[SEQ_COLS + STATIC].values.astype(np.float32)
    ok = ~np.isnan(X).any(axis=1)
    Xs = scaler.transform(X[ok])
    sq = torch.tensor(Xs[:, :24].reshape(-1, LOOKBACK, 2).transpose(0, 2, 1))
    st = torch.tensor(Xs[:, 24:].astype(np.float32))
    y = sub.loc[ok, LABELS].values

    with torch.no_grad():
        out = []
        for j in range(0, len(sq), 32768):
            z = body(sq[j:j + 32768]).squeeze(-1)
            out.append(head(torch.cat([z, st[j:j + 32768]], 1)).numpy())
    p = np.concatenate(out)

    for h in range(3):
        au = roc_auc_score(y[:, h], p[:, h])
        ap = average_precision_score(y[:, h], p[:, h])
        csv_au = float(ref[(ref['fold'] == i + 1) & (ref['horizon_h'] == h + 1)]['auroc'].iloc[0])
        if h == 0:
            print(f'{i+1:>4} {ty:>6} {int(ok.sum()):>9,} {y[:, h].mean():>7.3%} | '
                  f'{au:>12.4f} {csv_au:>11.4f} {au - csv_au:>+8.4f} | {ap:>12.4f}')
        rows.append({'fold': i + 1, 'year': ty, 'h': h + 1, 'auroc': au,
                     'auprc': ap, 'csv_auroc': csv_au})

r = pd.DataFrame(rows)
r['diff'] = r['auroc'] - r['csv_auroc']
print('\n■ horizon별 평균')
print(r.groupby('h')[['auroc', 'csv_auroc', 'auprc']].mean().round(4).to_string())
mx = r['diff'].abs().max()
print(f'\nAUROC 최대 차이 {mx:.4f}')
print('→ 로드 정상. 차이는 재표본화 테스트셋 vs 연도 전체의 차이다.' if mx < 0.12
      else '→ ⚠ 차이가 크다. 가중치·스케일러·피처 순서를 재확인해야 한다.')
