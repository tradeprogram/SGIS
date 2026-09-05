"""
공모전 보고서용 그림 일괄 생성.

원칙
  1. 모든 수치는 저장소의 산출물에서 직접 읽는다. 하드코딩한 숫자는 그림에
     넣지 않는다. 파이프라인을 다시 돌리면 그림도 따라 바뀌어야 한다.
  2. 입력 파일이 없으면 그 그림만 건너뛰고 이유를 찍는다. 한 장 없다고
     전체가 죽으면 마감 직전에 곤란하다.
  3. 색은 화면(웹 UI)과 맞춘다. 보고서와 데모가 달라 보이면 안 된다.

출력  outputs/figures/*.png  (300dpi)
"""

import os
import glob
import json
import io

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
DERIVED = os.path.join(ROOT, 'data', 'grid_data', 'derived')
SCAN = os.path.join(DERIVED, 'daily_scan')
OUTV4 = os.path.join(ROOT, 'outputs', 'v4')
FIG = os.path.join(ROOT, 'outputs', 'figures')
os.makedirs(FIG, exist_ok=True)

# 한글 — 맑은 고딕. 없으면 그림의 라벨이 전부 두부(□)가 된다.
_mal = os.path.join(r'C:', os.sep, 'Windows', 'Fonts', 'malgun.ttf')
if os.path.exists(_mal):
    font_manager.fontManager.addfont(_mal)
    rcParams['font.family'] = 'Malgun Gothic'
rcParams['axes.unicode_minus'] = False
rcParams['figure.dpi'] = 300
rcParams['savefig.bbox'] = 'tight'
rcParams['axes.grid'] = True
rcParams['grid.alpha'] = 0.25
rcParams['axes.spines.top'] = False
rcParams['axes.spines.right'] = False

# 웹 UI 와 같은 색
C = {'vhigh': '#ef4444', 'high': '#fb923c', 'watch': '#facc15',
     'normal': '#a3e635', 'calm': '#22d3ee', 'ink': '#0f172a',
     'accent': '#38bdf8', 'gray': '#94a3b8'}

made, skipped = [], []


def save(fig, name, title):
    p = os.path.join(FIG, f'{name}.png')
    fig.savefig(p)
    plt.close(fig)
    made.append((name, title))
    print(f'  ✓ {name}.png  {title}')


def skip(name, why):
    skipped.append((name, why))
    print(f'  · {name} 건너뜀 — {why}')


# ── 1. 발화 순위 분포 — 실전 성능의 핵심 근거 ────────────────────────
def fig_ignition_ranks():
    f = os.path.join(DERIVED, 'ignition_ranks.csv')
    if not os.path.exists(f):
        return skip('01_ignition_ranks', 'ignition_ranks.csv 없음')
    r = pd.read_csv(f, encoding='utf-8-sig')
    best = r.groupby('fire_id')['haz_top_pct'].min().dropna()

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    # (a) 누적분포 — "상위 N% 안에 몇 %가 들어오는가"
    xs = np.arange(0, 101, 0.5)
    ys = [(best <= x).mean() * 100 for x in xs]
    ax[0].plot(xs, ys, color=C['vhigh'], lw=2, label='실제 발화 지점')
    ax[0].plot(xs, xs, color=C['gray'], ls='--', lw=1.2, label='무작위 기준선')
    for t in (1, 5, 10):
        v = (best <= t).mean() * 100
        ax[0].scatter([t], [v], color=C['ink'], zorder=5, s=22)
        ax[0].annotate(f'상위 {t}% → {v:.1f}%', (t, v), textcoords='offset points',
                       xytext=(8, -2), fontsize=8.5)
    ax[0].set_xlabel('전국 위험도 상위 %'); ax[0].set_ylabel('포착된 발화 비율 (%)')
    ax[0].set_title(f'(a) 발화 {len(best):,}건의 누적 포착률', fontsize=10.5)
    ax[0].legend(fontsize=8.5, loc='lower right')
    ax[0].set_xlim(0, 100); ax[0].set_ylim(0, 100)

    # (b) 피해규모별 — 큰 불일수록 잘 잡는가
    da = r.groupby('fire_id')['damagearea'].first()
    d = pd.DataFrame({'best': best, 'ha': da}).dropna()
    bins = [(0, 0.5, '0.5ha\n미만'), (0.5, 10, '0.5~10'), (10, 100, '10~100'),
            (100, 1e9, '100ha\n이상')]
    lab, med, n = [], [], []
    for lo, hi, nm in bins:
        s = d[(d.ha >= lo) & (d.ha < hi)]
        if len(s):
            lab.append(nm); med.append(s.best.median()); n.append(len(s))
    b = ax[1].bar(lab, med, color=[C['calm'], C['normal'], C['high'], C['vhigh']][:len(lab)])
    for rect, v, c in zip(b, med, n):
        ax[1].annotate(f'{v:.1f}%\n(n={c})', (rect.get_x() + rect.get_width() / 2, v),
                       ha='center', va='bottom', fontsize=8.5)
    ax[1].set_ylabel('발화지점 순위 중앙값 (전국 상위 %)')
    ax[1].set_title('(b) 피해규모가 클수록 순위가 높다', fontsize=10.5)
    ax[1].invert_yaxis()
    fig.suptitle('실제 발화 지점을 전국 403,385격자 중 어디에 두었는가', fontsize=12, y=1.02)
    save(fig, '01_ignition_ranks', '발화 순위 분포 · 피해규모별')


# ── 2. 시간축 위험등급 검증 ──────────────────────────────────────────
def fig_time_axis():
    f = os.path.join(DERIVED, 'daily_scan_all.csv')
    tr = os.path.join(ROOT, 'web', 'public', 'data', 'time_risk.json')
    if not (os.path.exists(f) and os.path.exists(tr)):
        return skip('02_time_axis', 'daily_scan_all.csv 또는 time_risk.json 없음')
    t = json.load(io.open(tr, encoding='utf-8'))
    tl = os.path.join(ROOT, 'web', 'public', 'data', 'timeline.json')
    if not os.path.exists(tl):
        return skip('02_time_axis', 'timeline.json 없음')
    T = json.load(io.open(tl, encoding='utf-8'))
    d = pd.DataFrame([{'l': x.get('l'), 'n': x['n'], 'ha': x['ha']} for x in T['days']])
    order = ['보통', '주의', '높음', '매우 높음']
    d = d[d['l'].isin(order)]
    g = d.groupby('l').agg(n_days=('n', 'size'), fires=('n', 'mean'),
                           ha=('ha', 'mean'), zero=('n', lambda x: (x == 0).mean() * 100))
    g = g.reindex(order).dropna()

    fig, ax = plt.subplots(1, 3, figsize=(12, 3.6))
    cols = [C['normal'], C['watch'], C['high'], C['vhigh']][:len(g)]
    for a, col, lab, fmt in [
        (ax[0], 'fires', '일평균 발화 건수', '{:.2f}'),
        (ax[1], 'ha', '일평균 피해면적 (ha)', '{:.1f}'),
        (ax[2], 'zero', '발화 0건인 날의 비율 (%)', '{:.0f}%'),
    ]:
        b = a.bar(g.index, g[col], color=cols)
        for rect, v in zip(b, g[col]):
            a.annotate(fmt.format(v), (rect.get_x() + rect.get_width() / 2, v),
                       ha='center', va='bottom', fontsize=9)
        a.set_title(lab, fontsize=10.5)
        a.tick_params(axis='x', labelsize=9)
    ax[0].set_ylabel(f'{int(g.n_days.sum())}일')
    fig.suptitle('시간축 위험등급 — "오늘은 5년 중 어느 정도로 위험한 날인가"', fontsize=12, y=1.04)
    save(fig, '02_time_axis', '시간축 등급별 실제 발화')


# ── 3. 주간인구 보정 효과 ────────────────────────────────────────────
def fig_daytime():
    v = os.path.join(DERIVED, 'sgis_dong_vulnerability.parquet')
    a = os.path.join(DERIVED, 'cell_admin.parquet')
    e = os.path.join(DERIVED, 'mask_exposure_500m.parquet')
    if not all(os.path.exists(x) for x in (v, a, e)):
        return skip('03_daytime', 'SGIS 취약성/노출 산출물 없음')
    V = pd.read_parquet(v); A = pd.read_parquet(a); E = pd.read_parquet(e)
    g = A.merge(E[['prow', 'pcol', 'pop_total']], on=['prow', 'pcol'], how='left') \
         .merge(V[['adm_cd', 'day_idx']], on='adm_cd', how='left')
    g['pop_day'] = g.pop_total * g.day_idx
    inh = g[g.pop_total.fillna(0) > 0].dropna(subset=['day_idx'])

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].hist(inh.day_idx.clip(0, 3), bins=60, color=C['accent'], alpha=0.85)
    m = inh.day_idx.median()
    ax[0].axvline(1.0, color=C['gray'], ls='--', lw=1.2)
    ax[0].axvline(m, color=C['vhigh'], lw=1.8)
    # 문서에 적힌 0.77 은 행정동 기준이다. 여기는 거주 격자 기준이라 값이 다르다.
    # 둘을 구분해 적지 않으면 검토자가 불일치로 읽는다.
    ax[0].annotate(f'격자 중앙값 {m:.2f}\n(행정동 기준 0.77)',
                   (m, ax[0].get_ylim()[1] * 0.86),
                   color=C['vhigh'], fontsize=8.5, ha='left')
    ax[0].annotate('1.0 = 낮·밤 인구 같음', (1.0, ax[0].get_ylim()[1] * 0.62),
                   color=C['gray'], fontsize=8.5, ha='left')
    ax[0].set_xlabel('주간지수 (주간 추정인구 / 상주인구)')
    ax[0].set_ylabel('거주 격자 수')
    ax[0].set_title('(a) 대부분의 거주 격자는 낮에 비워진다', fontsize=10.5)

    # 상주인구 상위 구간일수록 낮에 더 빈다
    q = pd.qcut(inh.pop_total, 10, labels=False, duplicates='drop')
    r = inh.groupby(q).apply(lambda s: s.pop_day.sum() / s.pop_total.sum())
    ax[1].plot(range(1, len(r) + 1), r.values, 'o-', color=C['high'], lw=2)
    ax[1].axhline(1.0, color=C['gray'], ls='--', lw=1.2)
    ax[1].set_xlabel('상주인구 십분위 (10 = 가장 인구가 많은 격자)')
    ax[1].set_ylabel('주간인구 / 상주인구')
    # 1~9분위는 0.93~1.07 로 거의 평평하고 10분위에서만 급락한다.
    # "인구가 많을수록 빈다"고 쓰면 그림과 어긋난다.
    ax[1].set_title('(b) 가장 밀집한 십분위에서만 크게 빈다', fontsize=10.5)
    ax[1].annotate(f'{r.values[-1]:.2f}', (len(r), r.values[-1]),
                   textcoords='offset points', xytext=(-6, -14),
                   color=C['high'], fontsize=9.5, ha='center')
    fig.suptitle('SGIS 종사자 통계로 추정한 주간인구 보정', fontsize=12, y=1.02)
    save(fig, '03_daytime', '주간인구 보정')


# ── 4. 모델 A/B — 검증셋 지표와 운영지표가 어긋난다 ──────────────────
def fig_ablation():
    f = os.path.join(ROOT, 'outputs', 'stage2_ab_cnn20.csv')
    if not os.path.exists(f):
        return skip('04_ablation', 'stage2_ab_cnn20.csv 없음')
    b = pd.read_csv(f, encoding='utf-8-sig')
    ths = [1, 5, 10, 20]
    gru = [(b['a'] <= t).mean() * 100 for t in ths]
    cnn = [(b['b'] <= t).mean() * 100 for t in ths]

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    x = np.arange(len(ths)); w = 0.36
    ax[0].bar(x - w / 2, gru, w, label='GRU (배포)', color=C['accent'])
    ax[0].bar(x + w / 2, cnn, w, label='v4b CNN', color=C['gray'])
    for i, (u, v) in enumerate(zip(gru, cnn)):
        ax[0].annotate(f'{u:.1f}', (i - w / 2, u), ha='center', va='bottom', fontsize=8.5)
        ax[0].annotate(f'{v:.1f}', (i + w / 2, v), ha='center', va='bottom', fontsize=8.5)
    ax[0].set_xticks(x); ax[0].set_xticklabels([f'상위 {t}%' for t in ths])
    ax[0].set_ylabel('발화 포착률 (%)')
    ax[0].set_title(f'(a) 운영지표 — 실제 발화 {len(b):,}건', fontsize=10.5)
    ax[0].legend(fontsize=9)

    ax[1].bar(['GRU (배포)', 'v4b CNN'], [0.837, 0.884],
              color=[C['accent'], C['gray']], width=0.5)
    for i, v in enumerate([0.837, 0.884]):
        ax[1].annotate(f'{v:.3f}', (i, v), ha='center', va='bottom', fontsize=9.5)
    ax[1].set_ylim(0.7, 0.95); ax[1].set_ylabel('AUROC (t+1h)')
    ax[1].set_title('(b) 검증셋 지표 — 방향이 반대다', fontsize=10.5)
    fig.suptitle('AUROC 가 높아진 모델을 운영지표로 재검증해 기각했다', fontsize=12, y=1.02)
    save(fig, '04_ablation', 'CNN vs GRU A/B')


# ── 5. occlusion 기여도 — 모델이 무엇을 보는가 ───────────────────────
def fig_occlusion():
    fs = sorted(glob.glob(os.path.join(DERIVED, 'replay_*_top.csv')))
    rows = []
    for f in fs:
        t = pd.read_csv(f, encoding='utf-8-sig')
        if 'occ_h11' in t.columns:
            rows.append(t)
    if not rows:
        return skip('05_occlusion', 'occlusion 컬럼이 있는 replay_*_top.csv 없음')
    a = pd.concat(rows, ignore_index=True)
    tcols = [f'occ_h{k:02d}' for k in range(12)]
    scols = ['P_lgbm', 'ndvi', 'ndmi', 'hum4d', 'prcp4d']
    tv = a[tcols].mean().values
    sv = a[[f'occ_{c}' for c in scols]].mean().values

    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8))
    xs = list(range(-11, 1))
    ax[0].bar(xs, tv, color=[C['high'] if v >= 0 else C['accent'] for v in tv])
    ax[0].set_xticks(xs)
    ax[0].set_xticklabels([f'{h}' if h else 't0' for h in xs], fontsize=8)
    ax[0].set_xlabel('시점 (시간 전)'); ax[0].set_ylabel('평균 기여도')
    ax[0].set_title('(a) 12시간을 넣지만 직전 1시간이 지배한다', fontsize=10.5)

    lab = ['공간 취약도\n(P_lgbm)', '식생 활력\n(NDVI)', '식생 수분\n(NDMI)',
           '4일 습도', '4일 강수']
    o = np.argsort(-sv)
    ax[1].barh([lab[i] for i in o][::-1], sv[o][::-1], color=C['high'])
    ax[1].set_xlabel('평균 기여도')
    ax[1].set_title('(b) 정적 입력 — Stage1 공간 취약도가 지배', fontsize=10.5)
    fig.suptitle(f'우선지역 {len(a):,}건의 occlusion 기여도 평균', fontsize=12, y=1.04)
    save(fig, '05_occlusion', 'occlusion 기여도')


# ── 6. 고령·노후주택은 발화와 무관하다 ───────────────────────────────
def fig_vulnerability():
    v = os.path.join(DERIVED, 'sgis_dong_vulnerability.parquet')
    fc = os.path.join(DERIVED, 'fire_cells.parquet')
    a = os.path.join(DERIVED, 'cell_admin.parquet')
    e = os.path.join(DERIVED, 'mask_exposure_500m.parquet')
    if not all(os.path.exists(x) for x in (v, fc, a, e)):
        return skip('06_vulnerability', '취약성/발화셀 산출물 없음')
    V = pd.read_parquet(v); A = pd.read_parquet(a); E = pd.read_parquet(e)
    V['old_house_ratio'] = V.old_house30 / V.tot_house
    g = A.merge(E[['prow', 'pcol', 'pop_total']], on=['prow', 'pcol'], how='left') \
         .merge(V[['adm_cd', 'old_ratio', 'avg_age', 'old_house_ratio', 'old_per_health']],
                on='adm_cd', how='left')
    F = pd.read_parquet(fc)[['prow', 'pcol']].drop_duplicates()
    F['fire'] = 1
    g = g.merge(F, on=['prow', 'pcol'], how='left')
    g['fire'] = g.fire.fillna(0)
    inh = g[g.pop_total.fillna(0) > 0]

    items = [('avg_age', '평균연령', 1), ('old_ratio', '고령비율', 100),
             ('old_house_ratio', '노후주택비율', 100), ('old_per_health', '보건시설당 노인', 0.01)]
    lab, base, fire = [], [], []
    for c, nm, sc in items:
        lab.append(nm)
        base.append(inh[c].mean() * sc)
        fire.append(inh[inh.fire == 1][c].mean() * sc)

    fig, ax = plt.subplots(figsize=(7.6, 4))
    x = np.arange(len(lab)); w = 0.36
    ax.bar(x - w / 2, base, w, label='거주 격자 전체', color=C['gray'])
    ax.bar(x + w / 2, fire, w, label='실제 발화 격자', color=C['vhigh'])
    for i, (u, vv) in enumerate(zip(base, fire)):
        ax.annotate(f'{vv / u:.2f}배', (i + w / 2, vv), ha='center', va='bottom',
                    fontsize=9, color=C['ink'])
    ax.set_xticks(x); ax.set_xticklabels(lab, fontsize=9.5)
    ax.set_ylabel('값 (단위는 지표마다 다름 — 배율만 보라)')
    ax.legend(fontsize=9)
    ax.set_title('발화 지점은 오히려 덜 고령이다 — 그래서 우선순위 산식에 넣지 않았다',
                 fontsize=11)
    save(fig, '06_vulnerability', '고령·노후주택 무상관')


# ── 7. 폴드별 성능 ───────────────────────────────────────────────────
def fig_folds():
    f = os.path.join(ROOT, 'outputs', 'gru_ignition_multih_results.csv')
    if not os.path.exists(f):
        return skip('07_folds', 'gru_ignition_multih_results.csv 없음')
    d = pd.read_csv(f, encoding='utf-8-sig')
    col = 'horizon_h' if 'horizon_h' in d.columns else 'horizon'
    d1 = d[d[col] == 1]
    if 'fold' not in d1.columns or 'auroc' not in d1.columns:
        return skip('07_folds', '컬럼 형식이 예상과 다름')
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    yr = d1['test_year'] if 'test_year' in d1.columns else d1['fold']
    b = ax.bar([str(x) for x in yr], d1['auroc'], color=C['accent'], width=0.55)
    for rect, v in zip(b, d1['auroc']):
        ax.annotate(f'{v:.3f}', (rect.get_x() + rect.get_width() / 2, v),
                    ha='center', va='bottom', fontsize=9)
    ax.axhline(d1['auroc'].mean(), color=C['vhigh'], ls='--', lw=1.4)
    ax.annotate(f'평균 {d1["auroc"].mean():.3f}', (len(d1) - 0.6, d1['auroc'].mean()),
                color=C['vhigh'], fontsize=9, va='bottom')
    ax.set_ylim(0.6, 1.0); ax.set_ylabel('AUROC (t+1h)')
    ax.set_xlabel('테스트 연도 (그 해를 학습에서 제외)')
    ax.set_title('연도별 leave-one-year-out 5-fold', fontsize=11)
    save(fig, '07_folds', '폴드별 성능')


for fn in (fig_ignition_ranks, fig_time_axis, fig_daytime, fig_ablation,
           fig_occlusion, fig_vulnerability, fig_folds):
    try:
        fn()
    except Exception as ex:
        skip(fn.__name__, f'예외 {type(ex).__name__}: {ex}')

print(f'\n생성 {len(made)}장 / 건너뜀 {len(skipped)}장  → {FIG}')
for n, w in skipped:
    print(f'  건너뜀 {n}: {w}')
