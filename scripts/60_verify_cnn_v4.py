"""
v4 CNN 재현 검증 — 내 구현이 교수님 Colab 산출물과 같은 값을 내는가.

왜 먼저 검증하는가
  가중치(state_dict)만 있고 모델 정의 코드는 없다. Conv1d 의 padding 이나
  활성함수 배치가 다르면 값이 조용히 달라진다. 다행히 fold5 의 2025년 전국
  공간추론 결과(cnn_v4_spatial_pred_2025.csv, 252,952행)가 있으므로,
  같은 입력에 같은 출력이 나오는지 대조하면 구조를 확정할 수 있다.

  이걸 건너뛰고 파이프라인을 갈아타면, 틀린 구조로 741일을 다시 돌린 뒤에야
  값이 이상하다는 걸 알게 된다.

구조 (state_dict 에서 읽어낸 것)
  body  0: Conv1d(2, 32, k=3)   3: Conv1d(32, 64, k=3)   → AdaptiveMaxPool1d(1)
  head  0: Linear(64+7=71, 64)  3: Linear(64, 3)
  1·2 번 자리는 파라미터가 없다 → ReLU / Dropout 계열

  padding 은 알 수 없으므로 0 과 1 두 가지를 모두 시험한다.
  AdaptiveMaxPool 이라 길이가 달라도 통과하기 때문에 오류로 걸러지지 않는다.
"""

import os, pickle, itertools
import numpy as np
import pandas as pd
import torch
import torch.nn as nn

ROOT   = r'C:\for_sgis'
SEQ    = os.path.join(ROOT, r'data\ml_dataset\seq_dataset_12h_multih_v2_allratios.parquet')
MDL    = os.path.join(ROOT, 'models_v4')
REF    = os.path.join(ROOT, r'outputs\v4\cnn_v4_spatial_pred_2025.csv')
TAG    = 'cnn_v4_r20_fold5'

LOOKBACK = 12
SEQ_COLS = []
for lag in range(LOOKBACK - 1, 0, -1):
    SEQ_COLS += [f'vpd_tm{lag}', f'wind_tm{lag}']
SEQ_COLS += ['vpd_t0', 'wind_t0']
STATIC = ['P_lgbm_r20', 'ndvi', 'ndmi', 'hum4d', 'prcp4d', 'doy_sin', 'doy_cos']
KEY = ['prow', 'pcol', 'year', 'month', 'day', 'hour']


def build(pad: int, drop: float):
    body = nn.Sequential(
        nn.Conv1d(2, 32, 3, padding=pad), nn.ReLU(), nn.Dropout(drop),
        nn.Conv1d(32, 64, 3, padding=pad), nn.ReLU(), nn.AdaptiveMaxPool1d(1),
    )
    head = nn.Sequential(
        nn.Linear(64 + len(STATIC), 64), nn.ReLU(), nn.Dropout(drop),
        nn.Linear(64, 3), nn.Sigmoid(),
    )
    return body, head


ref = pd.read_csv(REF)
print(f'참조 산출물 {len(ref):,}행  ({ref["year"].unique()})')

seq = pd.read_parquet(SEQ, columns=KEY + SEQ_COLS + STATIC)
mg = ref[KEY + ['y_prob_t1', 'y_prob_t2', 'y_prob_t3']].merge(seq, on=KEY, how='inner')
print(f'입력과 매칭된 행 {len(mg):,} / {len(ref):,}')
if len(mg) == 0:
    raise SystemExit('매칭 실패 — 키 구성 확인 필요')

with open(os.path.join(MDL, f'{TAG}_scaler.pkl'), 'rb') as f:
    scaler = pickle.load(f)

X = mg[SEQ_COLS + STATIC].values.astype(np.float32)
ok = ~np.isnan(X).any(axis=1)
print(f'결측 없는 행 {int(ok.sum()):,}')
Xs = scaler.transform(X[ok])
sq = torch.tensor(Xs[:, :24].reshape(-1, LOOKBACK, 2).transpose(0, 2, 1))  # (N, 2, 12)
st = torch.tensor(Xs[:, 24:].astype(np.float32))
ref_p = mg.loc[ok, ['y_prob_t1', 'y_prob_t2', 'y_prob_t3']].values

sd_b = torch.load(os.path.join(MDL, f'{TAG}_body.pt'), map_location='cpu')
sd_h = torch.load(os.path.join(MDL, f'{TAG}_head.pt'), map_location='cpu')

print(f'\n{"pad":>4} {"drop":>5} | {"최대오차":>12} {"평균오차":>12}  판정')
print('-' * 56)
best = None
for pad, drop in itertools.product([0, 1], [0.0, 0.2, 0.3]):
    body, head = build(pad, drop)
    try:
        body.load_state_dict(sd_b); head.load_state_dict(sd_h)
    except Exception as e:
        print(f'{pad:>4} {drop:>5} | state_dict 불일치: {e}'); continue
    body.eval(); head.eval()
    with torch.no_grad():
        out = []
        for i in range(0, len(sq), 16384):
            z = body(sq[i:i + 16384]).squeeze(-1)
            out.append(head(torch.cat([z, st[i:i + 16384]], 1)).numpy())
    p = np.concatenate(out)
    mx, mean = np.abs(p - ref_p).max(), np.abs(p - ref_p).mean()
    verdict = '✅ 일치' if mx < 1e-4 else ('근사' if mx < 1e-2 else '불일치')
    print(f'{pad:>4} {drop:>5} | {mx:12.2e} {mean:12.2e}  {verdict}')
    if best is None or mx < best[0]:
        best = (mx, pad, drop)

print()
if best and best[0] < 1e-4:
    print(f'★ 구조 확정: padding={best[1]}  (dropout 은 eval 모드라 값에 무관)')
    print('  → 이 구조로 32/35/51번의 Stage2 를 CNN 으로 교체하면 된다.')
else:
    print(f'⚠ 어떤 조합도 재현하지 못했다 (최소 오차 {best[0]:.2e}).')
    print('  활성함수·풀링 위치가 다를 수 있다. Colab 노트북의 모델 정의가 필요하다.')
