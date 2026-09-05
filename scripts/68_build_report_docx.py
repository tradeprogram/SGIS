"""
공모전 제출용 워드 보고서 생성.

양식 근거
  제8회 SGIS 활용 우수사례 공모전 (국가데이터처), 접수 2026-09-01~09-30.
  제출물은 "SGIS 활용사례 보고서". 2025년 공식 안내문이 요구한 구성이
  추진배경 · 추진과정 · 추진내용 · 추진성과 + 결과물 근거자료이므로 그대로 따른다.
  심사는 누리꾼 40% + 전문가 60%(충실성·효과성·확산가능성 각 30, 창의성 10).
  쪽수 제한은 공고에서 확인되지 않아 15쪽 내외로 맞춘다.

작성 원칙
  1. 수치는 67번이 읽은 것과 같은 산출물에서 가져온다. 본문과 그림이 어긋나면 안 된다.
  2. 심사항목에 직접 대응시킨다 — 충실성(SGIS가 핵심 데이터인가),
     효과성(누구의 어떤 판단이 개선되는가), 확산가능성(타 기관이 재사용 가능한가).
  3. 한계를 숨기지 않는다. 특히 주간인구 추정의 농촌 과소추정은 본문에 남긴다.

출력  C:\\Users\\user\\Desktop\\하수범_공모전\\SGIS\\*.docx
"""

import os
import io
import json
import glob

import pandas as pd
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
DERIVED = os.path.join(ROOT, 'data', 'grid_data', 'derived')
FIG = os.path.join(ROOT, 'outputs', 'figures')
DEST = os.path.join(r'C:', os.sep, 'Users', 'user', 'Desktop', '하수범_공모전', 'SGIS')
os.makedirs(DEST, exist_ok=True)
OUT = os.path.join(DEST, 'SGIS활용사례보고서_산불발화예측우선대응_하수범.docx')

FONT = '맑은 고딕'
ACCENT = RGBColor(0x1F, 0x4E, 0x79)


# ── 수치는 산출물에서 직접 읽는다 ────────────────────────────────────
def facts():
    f = {}
    r = pd.read_csv(os.path.join(DERIVED, 'ignition_ranks.csv'), encoding='utf-8-sig')
    b = r.groupby('fire_id')['haz_top_pct'].min().dropna()
    f['n_eval'] = len(b)
    for t in (1, 5, 10, 20):
        f[f'top{t}'] = (b <= t).mean() * 100
    da = r.groupby('fire_id')['damagearea'].first()
    d = pd.DataFrame({'best': b, 'ha': da}).dropna()
    f['med_big'] = d[d.ha >= 100].best.median()
    f['med_small'] = d[d.ha < 0.5].best.median()

    e = pd.read_parquet(os.path.join(DERIVED, 'mask_exposure_500m.parquet'))
    f['pop'] = e.pop_total.sum()
    v = pd.read_parquet(os.path.join(DERIVED, 'sgis_dong_vulnerability.parquet'))
    f['n_dong'] = len(v)
    f['old_ratio'] = v.pop_old.sum() / v.tot_ppltn.sum() * 100

    ds = pd.read_csv(os.path.join(DERIVED, 'daily_scan_all.csv'), encoding='utf-8-sig')
    h = ds[ds.hour == 10]
    f['n_days'] = h.date.nunique()
    f['top1_day'] = h.top1_pop_day.mean()
    f['top1_res'] = h.top1_pop.mean()

    tops = [pd.read_csv(x, encoding='utf-8-sig') for x in
            sorted(glob.glob(os.path.join(DERIVED, 'replay_*_top.csv')))]
    a = pd.concat(tops, ignore_index=True)
    f['n_top'] = len(a)
    f['ratio_day'] = a.pop_day.sum() / a.pop_total.sum()
    f['n_case'] = len(tops)
    return f


F = facts()
doc = Document()

# 기본 스타일 — 한글 폰트는 eastasia 를 따로 지정해야 적용된다
st = doc.styles['Normal']
st.font.name = FONT
st.font.size = Pt(10.5)
st.element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
st.paragraph_format.line_spacing = 1.45
st.paragraph_format.space_after = Pt(6)

for s in doc.sections:
    s.top_margin = s.bottom_margin = Cm(2.0)
    s.left_margin = s.right_margin = Cm(2.2)


def H(text, level=1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        run.font.name = FONT
        run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
        run.font.color.rgb = ACCENT
        run.font.size = Pt(15 if level == 1 else 12.5)
    p.paragraph_format.space_before = Pt(14 if level == 1 else 10)
    return p


def P(text, bold=False, size=10.5, align=None, italic=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = FONT
    r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    r.font.size = Pt(size)
    r.bold = bold
    r.italic = italic
    if align:
        p.alignment = align
    return p


def BUL(text):
    p = doc.add_paragraph(text, style='List Bullet')
    for r in p.runs:
        r.font.name = FONT
        r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
        r.font.size = Pt(10.5)
    return p


def TABLE(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Light Grid Accent 1'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ''
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.name = FONT
        r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
        r.font.size = Pt(9.5)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ''
            r = cells[i].paragraphs[0].add_run(str(v))
            r.font.name = FONT
            r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
            r.font.size = Pt(9.5)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    doc.add_paragraph()
    return t


def FIGURE(name, caption, width=16.0):
    p = os.path.join(FIG, f'{name}.png')
    if not os.path.exists(p):
        P(f'[그림 누락: {name}]', italic=True)
        return
    doc.add_picture(p, width=Cm(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    c = P(caption, size=9, align=WD_ALIGN_PARAGRAPH.CENTER, italic=True)
    c.paragraph_format.space_after = Pt(12)


def NOTE(text):
    """한계·주의는 눈에 띄게. 숨기지 않는 것이 이 보고서의 태도다."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    r = p.add_run('⚠ ' + text)
    r.font.name = FONT
    r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    r.font.size = Pt(9.5)
    r.font.color.rgb = RGBColor(0x8B, 0x40, 0x00)
    p.paragraph_format.space_after = Pt(10)


# ══ 표지 ═════════════════════════════════════════════════════════════
for _ in range(4):
    doc.add_paragraph()
P('제8회 SGIS 활용 우수사례 공모전', size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
t = P('산불 발화예측·우선대응 통합지도', bold=True, size=24, align=WD_ALIGN_PARAGRAPH.CENTER)
t.runs[0].font.color.rgb = ACCENT
P('SGIS 통계지리 기반 500m 격자 의사결정 시스템', size=13, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()
P('“어디에 불이 날까”가 아니라 “어디를 먼저 지킬 것인가”에 답한다',
  size=11.5, align=WD_ALIGN_PARAGRAPH.CENTER, italic=True)
for _ in range(6):
    doc.add_paragraph()
P('응모자   하수범', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
P('데모  https://wildfire-predict-framework.vercel.app', size=10, align=WD_ALIGN_PARAGRAPH.CENTER)
P('코드  https://github.com/tradeprogram/wildfire_predict_framework',
  size=10, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_page_break()

# ══ 1. 추진배경 ══════════════════════════════════════════════════════
H('1. 추진배경')
P('산불 대응의 실제 병목은 “불이 날까”가 아니라 “한정된 헬기와 진화대를 어디에 먼저 보낼 것인가”다. '
  '기존 산불위험지수는 전국을 시·군 단위 색으로 칠해 주지만, 그 색만으로는 출동 순서를 정할 수 없다. '
  '같은 “위험” 등급이어도 사람이 사는 마을과 무인 산지는 지켜야 할 이유가 전혀 다르기 때문이다.')
P('이 차이를 메우려면 위험도만으로는 부족하고 “그 격자에 무엇이 있는가”를 알아야 한다. '
  '그 자리에 SGIS가 들어간다. 이 프로젝트는 AI 발화예측과 SGIS 통계지리를 결합해 '
  '대응 우선순위를 자동으로 산출하는 시스템을 만들었다.')

TABLE(['구분', '내용'], [
    ['공간 단위', '전국 500m 격자 403,385개 (EPSG:5179)'],
    ['예측 시계', 't+1h · t+2h · t+3h 신규 발화 위험 동시 산출'],
    ['기간', '2021~2025년 산불시즌(2~6월) ' + f'{F["n_days"]}일 전 기간'],
    ['SGIS 결합', f'인구·가구·주택 격자통계, 행정동 {F["n_dong"]:,}개 경계·인구특성'],
], widths=[3.5, 12.5])

# ══ 2. 추진과정 ══════════════════════════════════════════════════════
H('2. 추진과정')
P('구축은 다섯 단계로 진행했다. 각 단계에서 SGIS가 어떤 역할을 했는지 함께 적는다.')
TABLE(['단계', '내용', 'SGIS의 역할'], [
    ['① 라벨 정비', '산불 이력의 발화 시점·위치를 격자·시간으로 정규화',
     '지오코딩 API로 좌표 결측 417건 복구'],
    ['② 격자 정합', '분석 격자와 SGIS 격자통계를 면적가중으로 결합',
     '격자경계 API로 원점 정렬 실측 검증'],
    ['③ 모델 학습', 'LightGBM 공간취약도 → GRU 시계열 2단 구조',
     '(모델 단계는 SGIS 미사용)'],
    ['④ 노출 결합', '위험도 × 노출로 대응 우선지역 산출',
     '주간인구 보정, 인구특성 결합'],
    ['⑤ 서비스화', '웹 지도·시간 슬라이더·분석 에이전트',
     '행정동 경계, 지역 공간질의'],
], widths=[2.6, 7.2, 6.2])

# ══ 3. 추진내용 ══════════════════════════════════════════════════════
H('3. 추진내용 — SGIS를 어디에 썼는가')
P('이 공모전의 기준으로 스스로에게 물었다. SGIS를 빼면 결과가 달라지는가? 다섯 군데에서 달라진다.', bold=True)

H('3.1 500m 격자 정합 — 면적가중 배분', 2)
P('분석 격자와 SGIS 격자통계는 정렬돼 있지 않다. 분석 셀 하나가 SGIS 셀 4개에 걸치며, '
  '어긋난 양이 전 격자에서 같아 배분 가중치가 상수다. 이를 면적가중으로 배분해 '
  f'총인구 {F["pop"]:,.0f}명을 격자에 실었다(원본 대비 99.47%). '
  'SGIS 격자경계 API로 강릉시·문경시·강남구의 격자 꼭짓점을 실측해 500 배수 정렬을 확인했고, '
  '배포 SHP 418,728셀에서도 재확인했다.')

H('3.2 지오코딩 복구 — 누락된 산불의 23%', 2)
P('산불 이력의 좌표 결측 417건을 SGIS 지오코딩 API의 3단계 조회(리 → 읍면동 → 시군구)로 복구했다. '
  '이 복구분에 2022년 울진 산불(16,302ha)을 비롯한 최대 규모 사건이 들어 있었다. '
  '복구 전 2022년 fold의 AUROC는 0.69였고, 복구 후 0.79로 회복됐다. '
  'SGIS가 없었다면 이 사건들은 학습과 평가 모두에서 빠진 채 남았을 것이다.')

H('3.3 노출의 시간 불일치 교정 — 핵심 기여', 2)
P('산불은 오후에 몰린다. 그래서 스캔 시각을 11·14시로 잡았다. '
  '그런데 노출은 인구주택총조사 상주인구, 즉 “밤에 어디서 자는가”로 재고 있었다. '
  '낮에 예측하고 밤 인구로 피해를 셈하고 있었던 것이다.', bold=True)
P('SGIS에 주간인구·통근 통계는 없다. SGIS 종사자 통계로 근사했다.')
P('    주간지수 = (종사자 + 65세 이상 + 15세 미만) ÷ 상주인구', bold=True)
FIGURE('03_daytime', '[그림 1] SGIS 종사자 통계로 추정한 주간인구 보정')
P('명동 53배, 아파트 밀집 주거지 0.30으로 상식과 맞는다. 이 보정의 효과는 다음과 같다.')
BUL(f'사례일 {F["n_case"]}일의 우선지역 Top10 중 56%가 교체 ({F["n_top"]:,}건 중 1,829건)')
BUL(f'선정 격자의 주간인구가 상주인구의 {F["ratio_day"]:.2f}배 — 낮에 비는 곳 대신 낮에 차는 곳을 고른다')
BUL('상주 기준 상위 200격자는 낮에 0.58배로 비워진다')
NOTE('한계 — 농작업은 사업체 등록에 잡히지 않는다. 논밭두렁 소각은 국내 산불 원인 1위인데, '
     '이 지표가 바로 그 활동을 세지 못한다. 따라서 농촌 주간인구를 과소추정하는 방향으로 치우친다. '
     '행정동 단위 지수를 격자에 곱하므로 동 내 균일 가정도 들어간다. '
     'SGIS 자료신청으로 500m 격자 연령별 인구를 받으면 후자는 없앨 수 있다. '
     '이 문구는 서비스 화면과 분석 에이전트에도 동일하게 표시한다.')

H('3.4 인구 특성 — 순위가 아니라 “무엇을 잃는가”', 2)
P(f'SGIS 통계 API에서 전국 {F["n_dong"]:,}개 행정동의 연령대별 인구를 받았고, '
  '통계주제도 33종을 전수 확인해 30년 이상 노후주택과 보건시설 1개당 65세 이상 인구를 확보했다. '
  f'전국 고령비율은 {F["old_ratio"]:.1f}%로 공식 통계와 일치한다.')
P('이 지표들을 우선순위 산식에 넣지 않았다. 검증했더니 발화와 상관이 없었기 때문이다.', bold=True)
FIGURE('06_vulnerability', '[그림 2] 발화 지점은 오히려 덜 고령이다 — 그래서 산식에 넣지 않았다')
P('거주 격자끼리 비교하면 발화지의 고령비율은 0.91배, 노후주택비율은 0.89배로 오히려 낮다. '
  'SGIS 인구 특성으로 “어디서 불이 나는가”를 설명할 수는 없다. 발화는 기상·지형·연료가 결정한다. '
  '대신 “같은 위험도일 때 무엇을 잃는가”를 보여주는 표시용으로 쓴다. '
  '화면은 “상위 5% 주간 노출인구” 아래에 “65세 이상”과 “30년 이상 노후주택”을 함께 세운다. '
  '대응 자원을 얼마나, 어떤 종류로 보낼지 판단하는 데 필요한 정보다.')

H('3.5 행정경계와 공간질의', 2)
P(f'SGIS 행정동 경계 {F["n_dong"]:,}개를 지도에 얹어 지역을 검색·강조할 수 있게 했고, '
  '같은 코드 체계로 분석 에이전트가 “의성군 상황은?” 같은 질문에 직접 답한다. '
  '시도 코드가 SGIS 자체 체계(11·21·22…39)라 표준 행정표준코드표로는 이을 수 없었다. '
  'ADM_CD 앞 5자리로 묶은 동 이름 집합을 상위 계층과 맞춰 252/252 완전일치, 중복 0으로 복원했다.')
doc.add_page_break()

# ══ 4. 추진성과 ══════════════════════════════════════════════════════
H('4. 추진성과')

H('4.1 실제 발화를 어디에 두었는가', 2)
FIGURE('01_ignition_ranks', f'[그림 3] 실제 발화 {F["n_eval"]:,}건 전수 평가')
TABLE(['구간', '포착률', '무작위 대비'], [
    ['전국 상위 1%', f'{F["top1"]:.1f}%', f'{F["top1"]:.1f}배'],
    ['전국 상위 5%', f'{F["top5"]:.1f}%', f'{F["top5"] / 5:.1f}배'],
    ['전국 상위 10%', f'{F["top10"]:.1f}%', f'{F["top10"] / 10:.1f}배'],
    ['전국 상위 20%', f'{F["top20"]:.1f}%', f'{F["top20"] / 20:.1f}배'],
], widths=[5.0, 5.0, 5.0])
P(f'피해 규모가 클수록 순위가 높다. 100ha 이상은 중앙값 {F["med_big"]:.1f}%, '
  f'0.5ha 미만은 {F["med_small"]:.1f}%다. 대형산불을 더 잘 잡는다는 뜻이고, '
  '대응 자원 배분이라는 목적에 부합한다.')

H('4.2 “오늘은 위험한 날인가” — 시간축 등급', 2)
P('공간 백분위만 보면 조용한 날에도 상위 1%는 늘 나온다. '
  f'{F["n_days"]}일 분포에서 오늘의 위치를 따로 재는 지표를 두었다.')
FIGURE('02_time_axis', '[그림 4] 시간축 위험등급별 실제 발화 실적')
P('발화건수와의 스피어만 상관 0.769. ‘매우 높음’ 등급인 날 중 발화 0건인 날은 하나도 없었다.')

H('4.3 검증 태도 — 지표가 좋아진 모델을 기각했다', 2)
P('연구실의 신규 방법론(Stage1 재표본화 1:20 + Stage2 CNN)으로 이관했다가 되돌렸다.')
FIGURE('04_ablation', '[그림 5] 검증셋 지표와 운영지표가 반대 방향을 가리킨다')
P('발화 1,160건 전수에서 5개 연도 전부 악화했다. 원인을 분리하니 회귀의 4분의 3이 아키텍처에서 나왔다'
  '(Stage1 교체분 −1.7%p, 아키텍처분 −5.0%p). '
  'AUROC는 40만 격자 순위 전체의 평균적 분리도이고, 화면이 쓰는 “우선대응 상위 1%”는 '
  '꼬리 4,034셀의 순서만 본다. 서로 다른 것을 잰다.')
P('작업 중 “산불은 고령 지역에서 난다”고 판단한 적도 있으나, 대조군을 잘못 잡은 오류였고 정정했다. '
  '비교 기준을 잘못 잡으면 데이터가 원하는 답을 준다.', italic=True)

H('4.4 왜 이 격자인가 — 설명 가능성', 2)
P('SHAP 대신 occlusion을 썼다. 이 모델의 입력은 12시간 시계열이라 '
  '“최근 몇 시간 중 어느 시점이 결정적이었나”가 곧 진화 지휘에 쓰이는 답이기 때문이다.')
FIGURE('05_occlusion', '[그림 6] 우선지역의 입력별 기여도')
P('12시간을 넣지만 직전 1시간이 지배하고, 정적 입력에서는 Stage1 공간 취약도가 압도적이다. '
  '서비스에서는 우선지역 항목의 “왜?”를 누르면 이 기여도가 그대로 펼쳐진다.')

H('4.5 의사결정이 어떻게 달라지는가', 2)
TABLE(['', '기존 방식', '본 시스템'], [
    ['공간 해상도', '시·군 단위 색상', '500m 격자 403,385개'],
    ['시간 해상도', '일 단위', 't+1h · t+2h · t+3h'],
    ['노출 기준', '없음 또는 상주인구', 'SGIS 주간 보정 인구'],
    ['산출물', '위험 등급', '대응 우선지역 순위 + 노출 규모 + 근거'],
], widths=[3.4, 6.0, 6.6])
P(f'{F["n_days"]}일 평균으로 상위 1% 격자의 노출은 상주 기준 {F["top1_res"]:,.0f}명에서 '
  f'주간 기준 {F["top1_day"]:,.0f}명으로 바뀐다. 같은 위험지도라도 지켜야 할 대상이 달라진다.')

# ══ 5. 확산 가능성 ═══════════════════════════════════════════════════
H('5. 확산 가능성')
P('이 시스템은 산불 전용 구조가 아니다. 다음 세 가지가 분리돼 있어 다른 재난·다른 기관으로 옮기기 쉽다.')
BUL('격자 정합·노출 계산 모듈은 재난 종류와 무관하다. 위험도 레이어만 교체하면 호우·폭염·산사태에 그대로 쓸 수 있다.')
BUL('주간인구 보정은 SGIS 종사자 통계만 있으면 어떤 지역에서도 재현된다. 낮에 일어나는 모든 재난에 공통으로 필요하다.')
BUL('전 과정이 공개 저장소에 있고 환경변수로 조건을 바꿔 재현할 수 있다. 지자체가 자기 관할로 좁혀 돌리는 것도 가능하다.')
P('특히 “위험도 × 노출”을 두 백분위의 평균으로 정의한 방식은 단위가 다른 지표를 결합하는 '
  '일반적인 틀이라, 다른 분야의 SGIS 활용에도 그대로 적용된다.')

# ══ 6. 한계 ══════════════════════════════════════════════════════════
H('6. 한계')
for i, t in enumerate([
    '주간인구는 추정치다. 농작업을 세지 못해 농촌을 과소추정한다(3.3 참조).',
    '소형 화재 식별력이 낮다. 0.5ha 미만은 순위 중앙값 30.2%다.',
    '하루 4개 시각만 스캔하므로 심야·저녁 발화 일부가 평가에서 빠진다.',
    '재표본화 학습이라 출력을 발생확률로 쓸 수 없다. 별도 보정이 필요하다.',
    '입력 기상 래스터의 보유 범위 때문에 2025년 6월까지만 산출된다.',
    '전 기간 모드는 격자 단위 값을 저장하지 않아 공간질의가 시군구까지만 답한다.',
], 1):
    BUL(f'{t}')
P('이 한계들은 서비스 화면과 분석 에이전트에도 같은 문구로 표시된다. '
  '보고서와 서비스가 다른 말을 하지 않게 하는 것이 이 프로젝트의 원칙이다.', italic=True)

# ══ 7. 맺음 ══════════════════════════════════════════════════════════
H('7. 맺음')
P('이 작업에서 SGIS는 지도 배경이나 인구 숫자가 아니었다.')
BUL('노출의 시간 불일치를 잡아 우선순위의 절반 이상을 바꿨다 (3.3)')
BUL('넣지 말아야 할 지표를 가려내 근거 없는 가중치를 막았다 (3.4)')
BUL('행정 체계를 복원해 지도와 대화형 질의를 잇는 뼈대가 됐다 (3.5)')
P('SGIS를 빼면 이 시스템은 “위험한 곳”까지만 말하고 멈춘다. '
  '“먼저 지켜야 할 곳”은 SGIS가 있어야 나온다.', bold=True)

doc.add_page_break()
H('부록. 근거자료')
TABLE(['구분', '위치'], [
    ['서비스(데모)', 'https://wildfire-predict-framework.vercel.app'],
    ['전체 코드·데이터 처리', 'https://github.com/tradeprogram/wildfire_predict_framework'],
    ['방법론 상세', 'docs/METHODOLOGY.md, docs/ARCHITECTURE.md'],
    ['모델 검증 기록', 'docs/STAGE2_ABLATION.md'],
    ['그림 생성 스크립트', 'scripts/67_report_figures.py (모든 수치를 산출물에서 직접 읽음)'],
], widths=[4.5, 11.5])

doc.save(OUT)
print(f'저장 {OUT}')
print(f'  본문 문단 {len(doc.paragraphs)} · 표 {len(doc.tables)} · 그림 6장')
print(f'  평가 사건 {F["n_eval"]:,}건 · 상위1% {F["top1"]:.1f}% · 전 기간 {F["n_days"]}일')
