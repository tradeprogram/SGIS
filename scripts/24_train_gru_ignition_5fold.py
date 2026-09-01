"""
신규발화 GRU 5-fold CV (t+1h/t+2h/t+3h 동시출력) — 14_gru_5fold_4v1_multih_colab.ipynb 로컬 이식.

14번과 동일하게 유지한 것 (기존 persistence 결과와 직접 비교하기 위함)
  - 아키텍처: GRU(2 vars → hidden 64, 2 layer, dropout 0.3) + FC(64+7 → 64 → 3), Sigmoid
  - 입력: vpd/wind × 12h 시퀀스 + 정적 7개(P_lgbm, ndvi, ndmi, hum4d, prcp4d, doy_sin, doy_cos)
  - 학습: RATIO=1:10, LR=3e-4, BATCH=2048, EPOCHS=100, PATIENCE=15, val=train의 random 20%
  - fold: 연도별 leave-one-year-out (4년 학습 + 1년 test)

14번에서 바꾼 것
  1) 데이터셋: seq_dataset_ignition_multih.parquet (신규발화 타깃)
  2) threshold 선정 방식 — 14번은 test set에서 F1이 최대가 되는 threshold를 다시 찾았다.
     이는 test 라벨을 보고 고른 값이라 외부검증 성능으로 주장할 수 없다.
     여기서는 validation에서 F1 최적 threshold를 정하고 test에 고정 적용한다.
     비교용으로 test에서 고른 값(_oracle)도 함께 기록하되, 보고서 수치는 val 기준을 쓴다.
  3) fold별 test 예측확률을 저장 → 이후 calibration/threshold 재분석에 사용
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

DS_PATH  = r'C:\for_sgis\data\grid_data\derived\seq_dataset_ignition_multih.parquet'
OUT_DIR  = r'C:\for_sgis\outputs'
MDL_DIR  = r'C:\for_sgis\models'
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(MDL_DIR, exist_ok=True)

DEVICE    = 'cuda' if torch.cuda.is_available() else 'cpu'
RATIO     = 10
LR        = 3e-4
EPOCHS    = 100
BATCH     = 2048
PATIENCE  = 15
SEED      = 42
VAL_RATIO = 0.2
torch.manual_seed(SEED)
np.random.seed(SEED)
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

print(f'디바이스: {DEVICE}  threads={torch.get_num_threads()}')
print(f'RATIO=1:{RATIO}  LR={LR}  EPOCHS={EPOCHS}  PATIENCE={PATIENCE}')

# ── 데이터 ───────────────────────────────────────────────────────────
df = pd.read_parquet(DS_PATH)
n_before = len(df)
pos_before = {L: int(df[L].sum()) for L in LABELS}
df = df.dropna(subset=ALL_FEATS + LABELS).reset_index(drop=True)
for L in LABELS:
    df[L] = df[L].astype(np.float32)
print(f'\n로드: {n_before:,} → dropna 후 {len(df):,}행')
print(f'  양성 변화: {pos_before} → {{' +
      ', '.join(f"'{L}': {int(df[L].sum())}" for L in LABELS) + '}')

any_pos = (df[LABELS].sum(axis=1) > 0)
pos_df  = df[any_pos].copy()
neg_df  = df[~any_pos].copy()
neg_sub = neg_df.sample(n=min(len(pos_df) * RATIO, len(neg_df)), random_state=SEED)
data    = pd.concat([pos_df, neg_sub], ignore_index=True)
print(f'샘플링: 양성={len(pos_df):,}  음성={len(neg_sub):,}  합계={len(data):,}')
print(f'  음성 구성: {neg_sub["sample_type"].value_counts().to_dict()}')

# ── 5-fold ───────────────────────────────────────────────────────────
all_results, all_probs = [], []
t0 = time.time()

for fd in FOLDS:
    fold_no, test_year, train_yrs = fd['fold'], fd['test'], fd['train']
    torch.manual_seed(SEED + fold_no)
    np.random.seed(SEED + fold_no)
    print(f"\n{'='*62}\nFold{fold_no}: train={train_yrs}  test={test_year}")

    tr_full = data[data['year'].isin(train_yrs)].reset_index(drop=True)
    te      = data[data['year'] == test_year].reset_index(drop=True)
    if te[LABELS].values.sum() == 0:
        print('  test 양성 없음 → 스킵')
        continue

    scaler = StandardScaler()
    tr_full[ALL_FEATS] = scaler.fit_transform(tr_full[ALL_FEATS].values)
    te[ALL_FEATS]      = scaler.transform(te[ALL_FEATS].values)

    strat = tr_full[LABELS].sum(axis=1).clip(upper=1).values.astype(int)
    tr_idx, val_idx = train_test_split(np.arange(len(tr_full)), test_size=VAL_RATIO,
                                       random_state=SEED + fold_no, stratify=strat)
    tr  = tr_full.iloc[tr_idx].reset_index(drop=True)
    val = tr_full.iloc[val_idx].reset_index(drop=True)
    print(f'  train={len(tr):,}  val={len(val):,}  test={len(te):,}  '
          f'test 양성={te[LABELS].values.sum():.0f}')

    tr_s  = torch.tensor(tr[SEQ_COLS].values.reshape(-1, N_STEP, N_FEAT).astype('float32'))
    tr_st = torch.tensor(tr[STATIC_COLS].values.astype('float32'))
    tr_y  = torch.tensor(tr[LABELS].values.astype('float32'))
    val_s  = torch.tensor(val[SEQ_COLS].values.reshape(-1, N_STEP, N_FEAT).astype('float32')).to(DEVICE)
    val_st = torch.tensor(val[STATIC_COLS].values.astype('float32')).to(DEVICE)
    val_y  = val[LABELS].values.astype('float32')
    te_s   = torch.tensor(te[SEQ_COLS].values.reshape(-1, N_STEP, N_FEAT).astype('float32')).to(DEVICE)
    te_st  = torch.tensor(te[STATIC_COLS].values.astype('float32')).to(DEVICE)
    te_y   = te[LABELS].values.astype('float32')

    mt0 = time.time()
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
            pred = gru_head(torch.cat([out[:, -1, :], st], 1))
            loss = criterion(pred, y)
            optimizer.zero_grad(); loss.backward(); optimizer.step()

        gru_enc.eval(); gru_head.eval()
        with torch.no_grad():
            out, _ = gru_enc(val_s)
            vp = gru_head(torch.cat([out[:, -1, :], val_st], 1)).cpu().numpy()
        val_auprc = float(np.mean([average_precision_score(val_y[:, k], vp[:, k])
                                   for k in range(N_OUT)]))
        if val_auprc > best_auprc:
            best_auprc, patience_cnt = val_auprc, 0
            best_states = (copy.deepcopy(gru_enc.state_dict()), copy.deepcopy(gru_head.state_dict()))
        else:
            patience_cnt += 1
            if patience_cnt >= PATIENCE:
                print(f'  Early stop @ epoch {epoch+1}')
                break

    gru_enc.load_state_dict(best_states[0]); gru_head.load_state_dict(best_states[1])
    gru_enc.eval(); gru_head.eval()
    with torch.no_grad():
        out, _ = gru_enc(val_s)
        val_prob = gru_head(torch.cat([out[:, -1, :], val_st], 1)).cpu().numpy()
        out, _ = gru_enc(te_s)
        te_prob = gru_head(torch.cat([out[:, -1, :], te_st], 1)).cpu().numpy()

    prob_rows = {'fold': fold_no, 'test_year': test_year,
                 'prow': te['prow'].values.astype(int), 'pcol': te['pcol'].values.astype(int),
                 'year': te['year'].values.astype(int), 'month': te['month'].values.astype(int),
                 'day': te['day'].values.astype(int), 'hour': te['hour'].values.astype(int),
                 'sample_type': te['sample_type'].values}
    for k, H in enumerate(HORIZONS):
        prob_rows[f'y_true_t{H}'] = te_y[:, k].astype(int)
        prob_rows[f'y_prob_t{H}'] = te_prob[:, k]
    all_probs.append(pd.DataFrame(prob_rows))

    if test_year == 2025:
        import pickle
        torch.save(gru_enc.state_dict(),  os.path.join(MDL_DIR, 'gru_ign_fold5_test2025_body.pt'))
        torch.save(gru_head.state_dict(), os.path.join(MDL_DIR, 'gru_ign_fold5_test2025_head.pt'))
        with open(os.path.join(MDL_DIR, 'gru_ign_fold5_test2025_scaler.pkl'), 'wb') as f:
            pickle.dump(scaler, f)
        print('  fold5 모델 저장')

    thrs = np.linspace(0.01, 0.99, 99)
    for k, H in enumerate(HORIZONS):
        yv, pv = val_y[:, k], val_prob[:, k]
        yt, pt = te_y[:, k], te_prob[:, k]

        # threshold는 validation에서 선정 → test에 고정 적용
        val_f1s = [f1_score(yv, (pv >= t).astype(int), zero_division=0) for t in thrs]
        thr_val = float(thrs[int(np.argmax(val_f1s))])
        pred = (pt >= thr_val).astype(int)
        tp = int(((pred == 1) & (yt == 1)).sum())
        fp = int(((pred == 1) & (yt == 0)).sum())
        fn = int(((pred == 0) & (yt == 1)).sum())

        # 비교용: test에서 직접 고른 threshold(낙관 편향 — 보고서 수치로 쓰지 말 것)
        te_f1s = [f1_score(yt, (pt >= t).astype(int), zero_division=0) for t in thrs]

        all_results.append({
            'fold': fold_no, 'test_year': test_year, 'horizon_h': H,
            'train_years': str(train_yrs), 'ratio': RATIO,
            'epochs_run': epoch + 1, 'elapsed_s': round(time.time() - mt0, 1),
            'n_test': int(len(yt)), 'n_pos_test': int(yt.sum()),
            'auroc': round(roc_auc_score(yt, pt), 4),
            'auprc': round(average_precision_score(yt, pt), 4),
            'threshold_val': round(thr_val, 3),
            'f1':  round(f1_score(yt, pred, zero_division=0), 4),
            'csi': round(tp / (tp + fp + fn + 1e-8), 4),
            'pod': round(tp / (tp + fn + 1e-8), 4),
            'far': round(fp / (fp + tp + 1e-8), 4),
            'f1_oracle': round(float(np.max(te_f1s)), 4),
            'threshold_oracle': round(float(thrs[int(np.argmax(te_f1s))]), 3),
        })
        r = all_results[-1]
        print(f"  t+{H}h: AUPRC={r['auprc']:.4f} AUROC={r['auroc']:.4f} "
              f"CSI={r['csi']:.4f} POD={r['pod']:.4f} FAR={r['far']:.4f} "
              f"thr(val)={r['threshold_val']:.2f} n_pos={r['n_pos_test']}")

# ── 저장·요약 ────────────────────────────────────────────────────────
res = pd.DataFrame(all_results)
res.to_csv(os.path.join(OUT_DIR, 'gru_ignition_multih_results.csv'),
           index=False, encoding='utf-8-sig')
pd.concat(all_probs, ignore_index=True).to_csv(
    os.path.join(OUT_DIR, 'gru_ignition_multih_probs.csv'), index=False, encoding='utf-8-sig')

print(f"\n{'='*62}\n신규발화 GRU 5-fold 결과  (총 {(time.time()-t0)/60:.1f}분)\n{'='*62}")
print(res[['fold', 'test_year', 'horizon_h', 'n_pos_test',
           'auprc', 'auroc', 'csi', 'pod', 'far', 'threshold_val']].to_string(index=False))
print('\n★ horizon별 5-fold 평균:')
print(res.groupby('horizon_h')[['auprc', 'auroc', 'csi', 'pod', 'far']].mean().round(4).to_string())
print('\n★ 연도별 3-horizon 평균:')
print(res.groupby('test_year')[['auprc', 'auroc', 'csi', 'pod', 'far']].mean().round(4).to_string())
print('\n[참고] threshold 선정 방식 비교 — val 기준 F1 vs test에서 고른 F1(낙관 편향):')
print(res.groupby('horizon_h')[['f1', 'f1_oracle']].mean().round(4).to_string())
