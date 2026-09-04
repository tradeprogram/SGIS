"""
Stage2 재학습 — 재표본화 비율 가변(기본 1:20), 아키텍처 가변(기본 CNN).

ARCH=gru 는 원인 분리용이다. v4b CNN 이 전국 격자 상위 1% 포착률에서
기존 GRU 파이프라인보다 나빴는데(5.3%→2.9%), 그때 Stage1(구 LGBM→r20)과
Stage2(GRU→CNN)가 동시에 바뀌어 있었다. 같은 데이터·같은 재표본화·같은
하이퍼파라미터로 GRU 를 학습해 두면 CNN 과의 차이가 순수하게 아키텍처 차이가 된다.
  A 구 GRU + 구 Stage1   (기존 기준, models/gru_ign_*)
  B    GRU + r20 + 1:20  (ARCH=gru)   ← A vs B = Stage1 교체분
  C    CNN + r20 + 1:20  (ARCH=cnn)   ← B vs C = 아키텍처분
GRU 원래 설정은 dropout 0.3 / lr 3e-4 였지만 여기서는 CNN 과 동일하게 맞춘다.
안 맞추면 아키텍처 차이인지 하이퍼파라미터 차이인지 다시 섞인다.

배경
  v4b 는 Stage1 LGBM 을 1:20(P_lgbm_r20)으로 쓰면서 Stage2 CNN 학습셋만
  1:10 으로 뽑았다. 두 단계의 비율을 1:20 으로 맞춘다.

교수님 Colab 설정 역산 (dl_cnn_v4b_allfold_results.csv 에서)
  · 양성   = label_t1/t2/t3 중 하나라도 1 → 13,318행
  · 음성   = 양성 x ratio 를 **연도 구분 없이 전역에서** 추출
             13,318 + 133,180 = 146,498 = CSV 5개 연도 n_test 합계와 정확히 일치
  · 폴드    = 재표본 결과를 연도로 자름. test Y, val Y+1(순환), train 나머지 3년
             (fold1 n_val 2022 = fold2 n_test 2022 = 35,827 → 재표본은 폴드 간 고정)
  · 배치    = 2048 (n_steps ÷ epochs_run 이 ceil(n_train/2048) 과 일치)
  · lr      = 5e-4, early_stop patience 15 (epochs_run - best_epoch 이 모두 15)

재현 불가능한 것 — 노트북이 없어서 모르는 값
  dropout, 시드, early-stop 기준(val AUROC vs val loss).
  그래서 RATIO=10 을 먼저 돌려 CSV AUROC 와 대조한다. 거기서 맞으면
  RATIO=20 결과도 믿을 수 있다.

사용
  RATIO=10 python scripts/62_train_cnn_v4_ratio.py    # 재현 검증용
  RATIO=20 python scripts/62_train_cnn_v4_ratio.py    # 본 학습
"""

import os, pickle, time
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
SEQ  = os.path.join(ROOT, 'data', 'ml_dataset', 'seq_dataset_12h_multih_v2_allratios.parquet')
MDL  = os.path.join(ROOT, 'models_v4')
OUT  = os.path.join(ROOT, 'outputs', 'v4')
os.makedirs(MDL, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

RATIO    = int(os.environ.get('RATIO', '20'))
SEED     = int(os.environ.get('SEED', '42'))
DROPOUT  = float(os.environ.get('DROPOUT', '0.2'))
ARCH     = os.environ.get('ARCH', 'cnn')               # cnn | gru
LR       = 5e-4
BATCH    = 2048
MAX_EP   = 200
PATIENCE = 15
TAGBASE  = f'{ARCH}_v4b_r20_s{RATIO}'

LOOKBACK = 12
SEQ_COLS = []
for lag in range(LOOKBACK - 1, 0, -1):
    SEQ_COLS += [f'vpd_tm{lag}', f'wind_tm{lag}']
SEQ_COLS += ['vpd_t0', 'wind_t0']
STATIC = ['P_lgbm_r20', 'ndvi', 'ndmi', 'hum4d', 'prcp4d', 'doy_sin', 'doy_cos']
LABELS = ['label_t1', 'label_t2', 'label_t3']
FEATS  = SEQ_COLS + STATIC
YEARS  = [2021, 2022, 2023, 2024, 2025]


def build(drop):
    """CNN 은 60번에서 fold5 공간추론 결과와 6.17e-07 오차로 확정한 구조."""
    if ARCH == 'gru':
        body = nn.GRU(2, 64, num_layers=2, batch_first=True, dropout=drop)
    else:
        body = nn.Sequential(
            nn.Conv1d(2, 32, 3, padding=1), nn.ReLU(), nn.Dropout(drop),
            nn.Conv1d(32, 64, 3, padding=1), nn.ReLU(), nn.AdaptiveMaxPool1d(1),
        )
    head = nn.Sequential(
        nn.Linear(64 + len(STATIC), 64), nn.ReLU(), nn.Dropout(drop),
        nn.Linear(64, 3), nn.Sigmoid(),
    )
    return body, head


def encode(body, sq):
    """CNN 은 채널이 앞(N,2,12)이고 pool 뒤 (N,64,1), GRU 는 (N,12,2)에 마지막 은닉."""
    if ARCH == 'gru':
        return body(sq)[0][:, -1, :]
    return body(sq).squeeze(-1)


def to_tensors(X):
    sq = X[:, :24].reshape(-1, LOOKBACK, 2)
    if ARCH != 'gru':
        sq = sq.transpose(0, 2, 1)
    return torch.tensor(sq.copy()), torch.tensor(X[:, 24:].copy())


@torch.no_grad()
def predict(body, head, sq, st, bs=32768):
    body.eval()
    head.eval()
    out = []
    for j in range(0, len(sq), bs):
        z = encode(body, sq[j:j + bs])
        out.append(head(torch.cat([z, st[j:j + bs]], 1)).numpy())
    return np.concatenate(out)


print(f'■ Stage2 {ARCH.upper()} 재학습  ratio 1:{RATIO}  seed {SEED}  dropout {DROPOUT}')
d = pd.read_parquet(SEQ, columns=['year'] + FEATS + LABELS)
d = d.dropna(subset=FEATS).reset_index(drop=True)
pos_mask = d[LABELS].sum(1).values > 0
n_pos = int(pos_mask.sum())
print(f'전체 {len(d):,}행 / 양성(t1~t3 중 하나) {n_pos:,}행')

# 전역 재표본화 — 폴드마다 다시 뽑지 않는다. 교수님 CSV 의 연도별 행 수가
# 폴드 간 동일한 것이 그 근거다.
rng = np.random.default_rng(SEED)
neg_idx = np.flatnonzero(~pos_mask)
take = min(len(neg_idx), n_pos * RATIO)
sel = np.concatenate([np.flatnonzero(pos_mask), rng.choice(neg_idx, take, replace=False)])
sel.sort()
ds = d.iloc[sel].reset_index(drop=True)
print(f'재표본 {len(ds):,}행 (양성 {n_pos:,} + 음성 {take:,})')
print('  연도별:', ds.groupby('year').size().to_dict())

Xall = ds[FEATS].values.astype(np.float32)
Yall = ds[LABELS].values.astype(np.float32)
yr   = ds['year'].values

# 전 기간 평가용(재표본 아님) — 파이프라인이 실제로 마주하는 분포다.
Xfull = d[FEATS].values.astype(np.float32)
Yfull = d[LABELS].values.astype(np.float32)
yrf   = d['year'].values

rows = []
for i, ty in enumerate(YEARS):
    vy = YEARS[(i + 1) % len(YEARS)]
    tr = ~np.isin(yr, [ty, vy])
    va = yr == vy
    te = yr == ty
    tag = f'{TAGBASE}_fold{i+1}_test{ty}'
    t0 = time.time()

    scaler = StandardScaler().fit(Xall[tr])
    sq_tr, st_tr = to_tensors(scaler.transform(Xall[tr]).astype(np.float32))
    sq_va, st_va = to_tensors(scaler.transform(Xall[va]).astype(np.float32))
    y_tr = torch.tensor(Yall[tr])
    y_va = Yall[va]

    torch.manual_seed(SEED + i)
    body, head = build(DROPOUT)
    opt = torch.optim.Adam(list(body.parameters()) + list(head.parameters()), lr=LR)
    lossf = nn.BCELoss()

    best, best_ep, bad, snap = -1.0, 0, 0, None
    n = len(sq_tr)
    g = torch.Generator().manual_seed(SEED + i)
    for ep in range(1, MAX_EP + 1):
        body.train()
        head.train()
        perm = torch.randperm(n, generator=g)
        for j in range(0, n, BATCH):
            b = perm[j:j + BATCH]
            opt.zero_grad()
            z = encode(body, sq_tr[b])
            loss = lossf(head(torch.cat([z, st_tr[b]], 1)), y_tr[b])
            loss.backward()
            opt.step()

        pv = predict(body, head, sq_va, st_va)
        au = float(np.mean([roc_auc_score(y_va[:, h], pv[:, h]) for h in range(3)]))
        if au > best:
            best, best_ep, bad = au, ep, 0
            snap = ({k: v.clone() for k, v in body.state_dict().items()},
                    {k: v.clone() for k, v in head.state_dict().items()})
        else:
            bad += 1
            if bad >= PATIENCE:
                break

    body.load_state_dict(snap[0])
    head.load_state_dict(snap[1])
    torch.save(body.state_dict(), os.path.join(MDL, f'{tag}_body.pt'))
    torch.save(head.state_dict(), os.path.join(MDL, f'{tag}_head.pt'))
    with open(os.path.join(MDL, f'{tag}_scaler.pkl'), 'wb') as f:
        pickle.dump(scaler, f)

    # 평가 두 가지 — 재표본 테스트셋(교수님 CSV 대조용)과 연도 전체(실사용 분포)
    sq_te, st_te = to_tensors(scaler.transform(Xall[te]).astype(np.float32))
    p_te = predict(body, head, sq_te, st_te)
    y_te = Yall[te]

    fm = yrf == ty
    sq_fu, st_fu = to_tensors(scaler.transform(Xfull[fm]).astype(np.float32))
    p_fu = predict(body, head, sq_fu, st_fu)
    y_fu = Yfull[fm]

    for h in range(3):
        rows.append({
            'model': ARCH.upper(), 'fold': i + 1, 'test_year': ty, 'val_year': vy,
            'horizon_h': h + 1, 'plgbm': 'P_lgbm_r20', 'ratio': RATIO, 'lr': LR,
            'n_train': int(tr.sum()), 'n_val': int(va.sum()), 'n_test': int(te.sum()),
            'n_pos_test': int(y_te[:, h].sum()),
            'auroc': roc_auc_score(y_te[:, h], p_te[:, h]),
            'auprc': average_precision_score(y_te[:, h], p_te[:, h]),
            'full_n': int(fm.sum()), 'full_pos': int(y_fu[:, h].sum()),
            'full_auroc': roc_auc_score(y_fu[:, h], p_fu[:, h]),
            'full_auprc': average_precision_score(y_fu[:, h], p_fu[:, h]),
            'best_epoch': best_ep, 'dropout': DROPOUT, 'seed': SEED,
            'fold_sec': round(time.time() - t0, 1),
        })
    r1 = rows[-3]
    print(f"fold{i+1} test{ty} val{vy} | tr {r1['n_train']:>7,} te {r1['n_test']:>6,} | "
          f"best_ep {best_ep:>3} | t+1h AUROC 재표본 {r1['auroc']:.4f} / 전체 {r1['full_auroc']:.4f} | "
          f"{r1['fold_sec']:.0f}s")

res = pd.DataFrame(rows)
dst = os.path.join(OUT, f'dl_{ARCH}_v4b_s{RATIO}_allfold_results.csv')
res.to_csv(dst, index=False, encoding='utf-8-sig')
print(f'\n저장 {dst}')
print('\n■ horizon별 평균')
print(res.groupby('horizon_h')[['auroc', 'auprc', 'full_auroc', 'full_auprc']].mean().round(4).to_string())
print(f"\nt+1h 평균 AUROC  재표본 {res[res.horizon_h == 1]['auroc'].mean():.4f} / "
      f"연도전체 {res[res.horizon_h == 1]['full_auroc'].mean():.4f}")
