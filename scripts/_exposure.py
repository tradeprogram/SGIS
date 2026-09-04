"""
격자별 노출(Exposure) — 35·51번이 공유한다.

왜 주간 보정을 하는가
  이 시스템은 산불이 오후에 몰린다는 걸 알아서 스캔 시각을 11·14시로 잡았다.
  그런데 노출은 인구주택총조사 상주인구, 즉 '밤에 어디서 자는가'로 재고 있었다.
  낮에 예측하고 밤 인구로 피해를 셈한 셈이다.

  SGIS 에 주간인구·통근 통계는 없다(commute/daytimepopulation 엔드포인트 없음).
  65번이 종사자수로 근사한 day_idx 를 쓴다.
      day_idx = (종사자 + 고령 + 유년) / 상주인구
  전국 중앙값 0.77. 명동 53배, 아파트 밀집 주거지 0.30 으로 상식과 맞는다.

  한계 — 농작업은 사업체 등록에 안 잡힌다. 논밭에 나가 있는 사람(그게 산불
  원인 1위이기도 하다)을 못 세므로 농촌 주간인구를 과소추정하는 방향으로
  치우친다. 학생을 잔류로 둔 것도 근사고, 행정동 지수를 격자에 곱하므로
  동 안에서 균일하다는 가정이 들어간다. 화면에 같이 적어야 한다.

왜 고령·노후주택은 산식에 안 넣는가
  검증했더니 발화 지점과 상관이 없었다. 거주 격자끼리 비교하면 발화지의
  고령비율은 0.91배, 노후주택비율 0.89배로 오히려 낮다. 산식에 넣을 근거가
  없다. 대신 '같은 위험도일 때 무엇을 잃는가'를 보여주는 표시용으로만 쓴다.

  EXPO_MODE=resident 로 두면 주간 보정 없이 예전 동작으로 돌아간다.
"""

import os

import numpy as np
import pandas as pd

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
DERIVED = os.path.join(ROOT, 'data', 'grid_data', 'derived')
EXPO = os.path.join(DERIVED, 'mask_exposure_500m.parquet')
ADMIN = os.path.join(DERIVED, 'cell_admin.parquet')
VULN = os.path.join(DERIVED, 'sgis_dong_vulnerability.parquet')

MODE = os.environ.get('EXPO_MODE', 'day')      # day | resident

VULN_COLS = ['adm_cd', 'day_idx', 'old_ratio', 'old_house30', 'tot_house',
             'avg_age', 'oldage_suprt_per', 'old_per_health']


def build(valid_rows: np.ndarray, valid_cols: np.ndarray, verbose: bool = True) -> pd.DataFrame:
    """마스크 격자 순서 그대로 노출 프레임을 만든다.

    반환 컬럼
      pop_total   상주인구 (SGIS 500m 격자통계)
      pop_day     주간 보정 인구  = pop_total x day_idx
      pop_expo    산식에 쓰는 노출 (MODE 에 따라 위 둘 중 하나)
      pop_old     65세 이상    = pop_total x old_ratio
      old_house   30년이상 노후주택(호) = houses x 동의 노후주택 비율
      adm_cd/adm_nm, households, houses, low_count_only, avg_age 등
    """
    base = pd.DataFrame({'prow': valid_rows.astype(np.int32),
                         'pcol': valid_cols.astype(np.int32)})
    exp = pd.read_parquet(EXPO)
    keep = [c for c in ['prow', 'pcol', 'pop_total', 'households', 'houses', 'low_count_only']
            if c in exp.columns]
    base = base.merge(exp[keep], on=['prow', 'pcol'], how='left')

    adm = pd.read_parquet(ADMIN)
    base = base.merge(adm[['prow', 'pcol', 'adm_cd', 'adm_nm']].astype({'prow': 'int32', 'pcol': 'int32'}),
                      on=['prow', 'pcol'], how='left')

    v = pd.read_parquet(VULN)[VULN_COLS]
    # 노후주택은 동 단위 호수라 격자에 그대로 붙이면 안 된다. 동의 노후 비율을
    # 격자 주택수에 곱한다.
    v['old_house_ratio'] = v['old_house30'] / v['tot_house']
    base = base.merge(v.drop(columns=['old_house30', 'tot_house']), on='adm_cd', how='left')

    pop = base['pop_total'].fillna(0.0)
    # day_idx 가 없는 동(0.14%)은 보정하지 않는다. 1.0 으로 두면 상주인구
    # 그대로라 최소한 예전 동작과 같아진다.
    base['pop_day'] = pop * base['day_idx'].fillna(1.0)
    base['pop_old'] = pop * base['old_ratio'].fillna(0.0)
    base['old_house'] = base['houses'].fillna(0.0) * base['old_house_ratio'].fillna(0.0)
    base['pop_expo'] = base['pop_day'] if MODE == 'day' else pop

    if verbose:
        print(f'노출 레이어: {MODE} 기준 | 상주 {pop.sum():,.0f}명 '
              f'→ 주간 {base["pop_day"].sum():,.0f}명 ({base["pop_day"].sum()/pop.sum():.2f}배) '
              f'| 고령 {base["pop_old"].sum():,.0f}명')
    return base
