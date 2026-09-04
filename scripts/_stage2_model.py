"""
Stage1 LGBM + Stage2 CNN 로더 — 32/35/51번이 공유한다.

세 스크립트가 각자 모델 로드 코드를 복붙해 두고 있었다. 방법론이 v4b 로 바뀌면서
같은 수정을 세 군데에 해야 했고, 한 군데만 빠뜨리면 조용히 다른 결과가 나온다.
그래서 여기로 모은다.

현행 배포 = gru_old (아래 STAGE2_ARCH 참고)

v4b (검증 완료, 미채택)
  Stage1  LightGBM, 음성:양성 1:20  → P_lgbm_r20
  Stage2  CNN(Conv1d 2→32→64, k=3, padding=1 → AdaptiveMaxPool1d(1))
          + Linear(64+7→64) → Linear(64→3) → Sigmoid
          학습셋 재표본화 1:20 (STAGE2_RATIO)

STAGE2_ARCH
  cnn      cnn_v4b_r20_s{비율}_*        (기본)
  gru      gru_v4b_r20_s{비율}_*        62번 하네스로 학습한 GRU. 원인 분리용
  gru_old  models/gru_ign_* + 구 Stage1  기존 웹 자산을 만든 파이프라인
"""

import os
import pickle

import joblib
import numpy as np
import torch
import torch.nn as nn

NAS      = os.environ.get('NAS_ROOT', r'V:\data')
MDL_V4   = os.path.join(r'C:', os.sep, 'for_sgis', 'models_v4')
MDL_OLD  = os.path.join(r'C:', os.sep, 'for_sgis', 'models')

# 기본값은 gru_old — 배포 파이프라인이 쓰는 모델이다. v4b CNN 은 검증셋
# AUROC 가 더 높은데도 전국 격자 상위 1% 포착률이 5.3%→2.9% 로 떨어져
# 이관을 철회했다(docs/STAGE2_ABLATION.md). 기본값을 cnn 으로 두면
# 재실행하는 사람이 기각한 모델을 쓰게 된다.
ARCH      = os.environ.get('STAGE2_ARCH', 'gru_old')   # gru_old | cnn | gru
S2_RATIO  = os.environ.get('STAGE2_RATIO', '20')       # CNN 학습셋 재표본화 비율
S1_RATIO  = os.environ.get('STAGE1_RATIO', '20')       # LGBM 재표본화 비율

YEARS   = [2021, 2022, 2023, 2024, 2025]
N_OUT   = 3
N_STAT  = 7


def fold_of(year: int) -> int:
    """그 해를 학습에서 뺀 fold 를 고른다. 안 그러면 누수다."""
    if year not in YEARS:
        raise SystemExit(f'지원 연도 아님: {year} (2021~2025)')
    return YEARS.index(year) + 1


def lgbm_path(fold_no: int, year: int) -> str:
    if ARCH == 'gru_old':
        return os.path.join(NAS, 'ml_results', 'exp_no_smap_spi_temp_4v1',
                            'lgbm_models', f'lgbm_fold{fold_no}_test{year}.pkl')
    return os.path.join(NAS, 'ml_results', 'exp_no_smap_spi_temp_4v1_ne3000',
                        'lgbm_models', f'lgbm_r{S1_RATIO}_fold{fold_no}_test{year}.pkl')


def tag_of(fold_no: int, year: int) -> tuple:
    """(태그, 가중치 디렉터리)"""
    if ARCH == 'gru_old':
        return f'gru_ign_fold{fold_no}_test{year}', MDL_OLD
    return f'{ARCH}_v4b_r20_s{S2_RATIO}_fold{fold_no}_test{year}', MDL_V4


def _build_cnn():
    """60번에서 fold5 공간추론 결과와 6.17e-07 오차로 확정한 구조.
    padding=1 이 아니면 값이 조용히 달라진다."""
    body = nn.Sequential(
        nn.Conv1d(2, 32, 3, padding=1), nn.ReLU(), nn.Dropout(0.2),
        nn.Conv1d(32, 64, 3, padding=1), nn.ReLU(), nn.AdaptiveMaxPool1d(1),
    )
    head = nn.Sequential(
        nn.Linear(64 + N_STAT, 64), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(64, N_OUT), nn.Sigmoid(),
    )
    return body, head


def _build_gru():
    enc = nn.GRU(2, 64, num_layers=2, batch_first=True, dropout=0.3)
    head = nn.Sequential(nn.Linear(64 + N_STAT, 64), nn.ReLU(), nn.Dropout(0.3),
                         nn.Linear(64, N_OUT), nn.Sigmoid())
    return enc, head


def load(year: int, verbose: bool = True):
    """반환: (lgbm, scaler, infer, 설명문자열)

    infer(seq, static) -> (N,3) ndarray
      seq    (N,12,2) 스케일 전, [시점, (vpd,wind)] 순
      static (N,7)    스케일 전, [P_lgbm, ndvi, ndmi, hum4d, prcp4d, doy_sin, doy_cos]
    스케일러는 학습 때와 같은 [시퀀스 24 + 정적 7] 평탄화 순서로 먹인다.
    """
    fno = fold_of(year)
    tag, mdir = tag_of(fno, year)
    lp = lgbm_path(fno, year)
    for p in (lp, os.path.join(mdir, f'{tag}_body.pt'),
              os.path.join(mdir, f'{tag}_head.pt'),
              os.path.join(mdir, f'{tag}_scaler.pkl')):
        if not os.path.exists(p):
            raise SystemExit(f'모델 없음: {p}')

    lgbm = joblib.load(lp)
    with open(os.path.join(mdir, f'{tag}_scaler.pkl'), 'rb') as f:
        scaler = pickle.load(f)

    body, head = _build_cnn() if ARCH == 'cnn' else _build_gru()
    is_cnn = ARCH == 'cnn'
    body.load_state_dict(torch.load(os.path.join(mdir, f'{tag}_body.pt'), map_location='cpu'))
    head.load_state_dict(torch.load(os.path.join(mdir, f'{tag}_head.pt'), map_location='cpu'))
    body.eval()
    head.eval()

    def infer(seq: np.ndarray, static: np.ndarray, chunk: int = 8192) -> np.ndarray:
        n = len(seq)
        flat = np.concatenate([seq.reshape(n, -1), static], axis=1)
        s = scaler.transform(flat)
        sq = s[:, :24].reshape(n, 12, 2).astype(np.float32)
        st = s[:, 24:].astype(np.float32)
        if is_cnn:
            sq = sq.transpose(0, 2, 1).copy()      # (N,2,12) — Conv1d 는 채널이 앞
        out = np.zeros((n, N_OUT), np.float32)
        with torch.no_grad():
            for i in range(0, n, chunk):
                t = torch.tensor(sq[i:i + chunk])
                z = body(t).squeeze(-1) if is_cnn else body(t)[0][:, -1, :]
                out[i:i + chunk] = head(torch.cat([z, torch.tensor(st[i:i + chunk])], 1)).numpy()
        return out

    desc = (f'{"CNN v4b" if is_cnn else "GRU"} {tag} / {year}년을 학습에서 제외한 fold '
            f'| Stage1 {os.path.basename(lp)}')
    if verbose:
        print(f'모델: {desc}')
    return lgbm, scaler, infer, desc
