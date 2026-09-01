"""
하드네거티브 비중 스윕 — 학습 음성 구성만 바꿔 신규발화 GRU 성능 비교.

문제의식: 24번(baseline)은 전체 음성 534,860행에서 무작위로 1:10을 뽑았다.
          하드네거티브는 20,325행뿐이라 표본에 1,222개(3.4%)만 살아남았고,
          "발화지점 반경 1~10km 미발화 픽셀"이라는 설계 의도가 거의 작동하지 않았다.

비교 설계에서 반드시 지킬 것
  test set은 모든 설정에서 동일해야 한다. 하드네거티브를 test에도 늘리면
  문제 난이도 자체가 바뀌어 AUPRC가 떨어지고, 이를 성능 저하로 오독하게 된다.
  → test는 baseline 방식(해당 연도 음성에서 무작위 1:10)으로 고정하고,
    train/val의 음성 구성만 HARD_NEG_FRAC로 바꾼다.

HARD_NEG_FRAC = 학습 음성 중 하드네거티브가 차지할 목표 비율
                (None = baseline, 전체 음성풀에서 무작위)
"""

import os, time, copy, warnings
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score
warnings.filterwarnings('ignore')

DS_PATH = r'C:\for_sgis\data\grid_data\derived\seq_dataset_ignition_multih.parquet'
OUT_DIR = r'C:\for_sgis\outputs'
os.makedirs(OUT_DIR, exist_ok=True)

DEVICE    = 'cuda' if torch.cuda.is_available() else 'cpu'
RATIO     = 10
LR        = 3e-4
EPOCHS    = 100
BATCH     = 2048
PATIENCE  = 15
SEED      = 42
VAL_RATIO = 0.2
SETTINGS  = [None, 0.25, 0.50]      # None = baseline(무작위)
torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))

YEARS = [2021, 2022, 2023, 2024, 2025]
FOLDS = [{'fold': i+1, 'test': YEARS[i], 'train': [YEARS[j] for j in range(5) if j != i]}
         for i in range(5)]

SEQ_COLS = []
for lag in range(11, 0, -1):
    SEQ_COLS += [f'vpd_tm{lag}', f'wind_tm{lag}']
SEQ_COLS += ['vpd_t0', 'wind_t0']
STATIC_COLS = ['P_lgbm', 'ndvi', 'ndmi', 'hum4d', 'prcp4d', 'doy_sin', 'doy_cos']
ALL_FEATS   = SEQ_COLS + STATIC_COLS
HORIZONS    = [1, 2, 3]
LABELS      = [f'label_t{H}' for H in HORIZONS]
N_STEP, N_FEAT = 12, 2
N_STATIC, N_OUT = len(STATIC_COLS), len(LABELS)

df = pd.read_parquet(DS_PATH).dropna(subset=ALL_FEATS + LABELS).reset_index(drop=True)
for L in LABELS:
    df[L] = df[L].astype(np.float32)
df['_pos'] = (df[LABELS].sum(axis=1) > 0)
print(f'로드: {df.shape}  양성={int(df["_pos"].sum()):,}')
print(f'음성 풀: {df[~df["_pos"]]["sample_type"].value_counts().to_dict()}')

all_results = []
t_all = time.time()

for frac in SETTINGS:
    tag = 'baseline(random)' if frac is None else f'hardneg={frac:.2f}'
    print(f"\n{'#'*66}\n### 설정: {tag}\n{'#'*66}")

    for fd in FOLDS:
        fold_no, test_year, train_yrs = fd['fold'], fd['test'], fd['train']
        torch.manual_seed(SEED + fold_no)
        np.random.seed(SEED + fold_no)

        # ── test set: 모든 설정에서 동일 (seed 고정, baseline 방식) ──
        te_pos = df[(df['year'] == test_year) & df['_pos']]
        te_neg_pool = df[(df['year'] == test_year) & ~df['_pos']]
        te_neg = te_neg_pool.sample(n=min(len(te_pos) * RATIO, len(te_neg_pool)),
                                    random_state=SEED)
        te = pd.concat([te_pos, te_neg], ignore_index=True)

        # ── train/val: 음성 구성만 설정에 따라 변경 ──
        tr_pos = df[df['year'].isin(train_yrs) & df['_pos']]
        pool   = df[df['year'].isin(train_yrs) & ~df['_pos']]
        quota  = len(tr_pos) * RATIO

        if frac is None:
            tr_neg = pool.sample(n=min(quota, len(pool)), random_state=SEED)
            n_hard_used = int((tr_neg['sample_type'] == 'hard_neg').sum())
        else:
            hard_pool = pool[pool['sample_type'] == 'hard_neg']
            bg_pool   = pool[pool['sample_type'] == 'neg_bg']
            n_hard    = min(int(round(quota * frac)), len(hard_pool))
            n_bg      = min(quota - n_hard, len(bg_pool))
            tr_neg    = pd.concat([hard_pool.sample(n=n_hard, random_state=SEED),
                                   bg_pool.sample(n=n_bg, random_state=SEED)], ignore_index=True)
            n_hard_used = n_hard

        tr_full = pd.concat([tr_pos, tr_neg], ignore_index=True)
        print(f"\nFold{fold_no} test={test_year}  train={len(tr_full):,} "
              f"(하드네거티브 {n_hard_used:,} = 음성의 {100*n_hard_used/max(len(tr_neg),1):.1f}%)  "
              f"test={len(te):,} 양성={int(te['_pos'].sum()):,}")

        scaler = StandardScaler()
        tr_full = tr_full.copy(); te = te.copy()
        tr_full[ALL_FEATS] = scaler.fit_transform(tr_full[ALL_FEATS].values)
        te[ALL_FEATS]      = scaler.transform(te[ALL_FEATS].values)

        strat = tr_full[LABELS].sum(axis=1).clip(upper=1).values.astype(int)
        tr_idx, val_idx = train_test_split(np.arange(len(tr_full)), test_size=VAL_RATIO,
                                           random_state=SEED + fold_no, stratify=strat)
        tr  = tr_full.iloc[tr_idx].reset_index(drop=True)
        val = tr_full.iloc[val_idx].reset_index(drop=True)

        tr_s  = torch.tensor(tr[SEQ_COLS].values.reshape(-1, N_STEP, N_FEAT).astype('float32'))
        tr_st = torch.tensor(tr[STATIC_COLS].values.astype('float32'))
        tr_y  = torch.tensor(tr[LABELS].values.astype('float32'))
        val_s  = torch.tensor(val[SEQ_COLS].values.reshape(-1, N_STEP, N_FEAT).astype('float32')).to(DEVICE)
        val_st = torch.tensor(val[STATIC_COLS].values.astype('float32')).to(DEVICE)
        val_y  = val[LABELS].values.astype('float32')
        te_s   = torch.tensor(te[SEQ_COLS].values.reshape(-1, N_STEP, N_FEAT).astype('float32')).to(DEVICE)
        te_st  = torch.tensor(te[STATIC_COLS].values.astype('float32')).to(DEVICE)
        te_y   = te[LABELS].values.astype('float32')

        gru_enc = nn.GRU(N_FEAT, 64, num_layers=2, batch_first=True, dropout=0.3).to(DEVICE)
        gru_head = nn.Sequential(
            nn.Linear(64 + N_STATIC, 64), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(64, N_OUT), nn.Sigmoid()
        ).to(DEVICE)
        optimizer = torch.optim.Adam(list(gru_enc.parameters()) + list(gru_head.parameters()), lr=LR)
        criterion = nn.BCELoss()

        best_auprc, best_states, patience_cnt = -1, None, 0
        for epoch in range(EPOCHS):
            perm = torch.randperm(len(tr_y))
            gru_enc.train(); gru_head.train()
            for i in range(0, len(tr_y), BATCH):
                bi = perm[i:i+BATCH]
                s, st, y = tr_s[bi].to(DEVICE), tr_st[bi].to(DEVICE), tr_y[bi].to(DEVICE)
                out, _ = gru_enc(s)
                loss = criterion(gru_head(torch.cat([out[:, -1, :], st], 1)), y)
                optimizer.zero_grad(); loss.backward(); optimizer.step()

            gru_enc.eval(); gru_head.eval()
            with torch.no_grad():
                out, _ = gru_enc(val_s)
                vp = gru_head(torch.cat([out[:, -1, :], val_st], 1)).cpu().numpy()
            va = float(np.mean([average_precision_score(val_y[:, k], vp[:, k]) for k in range(N_OUT)]))
            if va > best_auprc:
                best_auprc, patience_cnt = va, 0
                best_states = (copy.deepcopy(gru_enc.state_dict()), copy.deepcopy(gru_head.state_dict()))
            else:
                patience_cnt += 1
                if patience_cnt >= PATIENCE:
                    break

        gru_enc.load_state_dict(best_states[0]); gru_head.load_state_dict(best_states[1])
        gru_enc.eval(); gru_head.eval()
        with torch.no_grad():
            out, _ = gru_enc(val_s)
            val_prob = gru_head(torch.cat([out[:, -1, :], val_st], 1)).cpu().numpy()
            out, _ = gru_enc(te_s)
            te_prob = gru_head(torch.cat([out[:, -1, :], te_st], 1)).cpu().numpy()

        thrs = np.linspace(0.01, 0.99, 99)
        for k, H in enumerate(HORIZONS):
            yv, pv = val_y[:, k], val_prob[:, k]
            yt, pt = te_y[:, k], te_prob[:, k]
            thr = float(thrs[int(np.argmax([f1_score(yv, (pv >= t).astype(int), zero_division=0)
                                            for t in thrs]))])
            pred = (pt >= thr).astype(int)
            tp = int(((pred == 1) & (yt == 1)).sum())
            fp = int(((pred == 1) & (yt == 0)).sum())
            fn = int(((pred == 0) & (yt == 1)).sum())
            all_results.append({
                'setting': tag, 'hardneg_frac': -1 if frac is None else frac,
                'fold': fold_no, 'test_year': test_year, 'horizon_h': H,
                'n_hardneg_train': n_hard_used, 'epochs_run': epoch + 1,
                'n_test': int(len(yt)), 'n_pos_test': int(yt.sum()),
                'auroc': round(roc_auc_score(yt, pt), 4),
                'auprc': round(average_precision_score(yt, pt), 4),
                'threshold_val': round(thr, 3),
                'f1':  round(f1_score(yt, pred, zero_division=0), 4),
                'csi': round(tp / (tp + fp + fn + 1e-8), 4),
                'pod': round(tp / (tp + fn + 1e-8), 4),
                'far': round(fp / (fp + tp + 1e-8), 4),
            })
        r3 = all_results[-3:]
        print('   ' + '  '.join(f"t+{x['horizon_h']}h AUPRC={x['auprc']:.3f}/AUROC={x['auroc']:.3f}"
                                for x in r3))

res = pd.DataFrame(all_results)
res.to_csv(os.path.join(OUT_DIR, 'gru_ignition_hardneg_sweep.csv'), index=False, encoding='utf-8-sig')

print(f"\n{'='*66}\n하드네거티브 비중 스윕 결과  (총 {(time.time()-t_all)/60:.1f}분)\n{'='*66}")
piv = res.groupby(['setting', 'horizon_h'])[['auprc', 'auroc', 'csi', 'pod', 'far']].mean().round(4)
print(piv.to_string())
print('\n★ 설정별 3-horizon 평균:')
print(res.groupby('setting')[['auprc', 'auroc', 'csi', 'pod', 'far']].mean().round(4).to_string())
print('\n★ 설정 × 연도별 AUROC:')
print(res.pivot_table(index='test_year', columns='setting', values='auroc').round(4).to_string())
print(f'\n저장: {os.path.join(OUT_DIR, "gru_ignition_hardneg_sweep.csv")}')
