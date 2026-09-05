"""
보고서용 서비스 화면 캡처.

왜 필요한가
  누리꾼 심사가 40%이고 안내문도 "그림·사진·그래프 등 시각적 자료 사용 권장"이라
  적었다. 성능 그래프만 있고 정작 서비스 화면이 없으면 무엇을 만들었는지 전달되지 않는다.

대상
  기본은 **배포 사이트**다. 분석 에이전트가 실제로 답하는 화면을 담으려면 API 키가
  있어야 하는데 로컬에는 없다(hasKey:false). 보고서에 오류 화면을 실을 수는 없다.
  로컬로 찍으려면 UI_URL=http://localhost:3100 으로 넘기되, 그때 에이전트 컷은
  인사말 상태로만 나온다. 개발 서버(3000)는 이 환경에서 하이드레이션에 실패한다.

deviceScaleFactor=2 로 잡아 인쇄에 견디는 해상도를 만든다.

출력  outputs/figures/ui/*.png
"""

import os
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.join(r'C:', os.sep, 'for_sgis')
OUT = os.path.join(ROOT, 'outputs', 'figures', 'ui')
os.makedirs(OUT, exist_ok=True)
URL = os.environ.get('UI_URL', 'https://wildfire-predict-framework.vercel.app')
W, H = 1440, 900

shots = []


def snap(page, name, desc, clip=None):
    p = os.path.join(OUT, f'{name}.png')
    page.screenshot(path=p, clip=clip)
    shots.append((name, desc))
    print(f'  ✓ {name}.png  {desc}')


def settle(page, ms=1800):
    page.evaluate("window.dispatchEvent(new Event('resize'))")
    page.wait_for_timeout(ms)


with sync_playwright() as pw:
    br = pw.chromium.launch()
    ctx = br.new_context(viewport={'width': W, 'height': H},
                         device_scale_factor=2, locale='ko-KR')
    page = ctx.new_page()
    page.goto(URL, wait_until='networkidle', timeout=90_000)
    # 지도 타일과 데이터가 다 붙을 때까지. networkidle 만으로는 이르다.
    page.wait_for_timeout(9000)
    settle(page)

    # ① 첫 화면 — 시간대별 예측 모드
    snap(page, '10_overview',
         '첫 화면: 시간대별 예측(2022-03-04 울진 산불 당일) · 좌측 SGIS 패널 · 우측 분석 에이전트')

    # ② 좌측 패널 — SGIS 노출 수치가 보이도록
    panel = page.locator('div.glass.hud.scroll-thin').first
    box = panel.bounding_box()
    if box:
        snap(page, '11_sgis_panel', '좌측 패널: 지역 찾기 · 위험등급 · SGIS 주간 노출인구/고령/노후주택',
             clip={'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height']})

    # ③ 우선지역 + XAI(occlusion) 펼침
    page.evaluate("""() => {
        const p=[...document.querySelectorAll('div')].find(d=>d.className.includes('scroll-thin')
                 && d.scrollHeight>d.clientHeight+50);
        if(p) p.scrollTop = p.scrollHeight;
    }""")
    page.wait_for_timeout(600)
    why = page.locator("button:has-text('왜?')")
    if why.count():
        why.first.click()
        page.wait_for_timeout(1500)
        page.evaluate("""() => {
            const w=[...document.querySelectorAll('div')].find(d=>d.innerText
                     && d.innerText.startsWith('각 입력을 기준값으로'));
            if(w) w.scrollIntoView({block:'center'});
        }""")
        page.wait_for_timeout(900)
        box = panel.bounding_box()
        if box:
            snap(page, '12_priority_xai',
                 '대응 우선지역 Top10 과 “왜?” — occlusion 기여도(최근 12시간 · 지역 조건)',
                 clip={'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height']})

    # ④ 격자 상세 — SGIS 노출과 모델 입력 신호
    snap(page, '13_cell_detail', '격자 클릭 시: 전국 상위 %, SGIS 인구·가구·주택, 모델이 실제로 본 입력값')

    # ⑤ 지역 검색 — 행정동 경계 강조
    page.evaluate("""() => {
        const p=[...document.querySelectorAll('div')].find(d=>d.className.includes('scroll-thin')
                 && d.scrollHeight>d.clientHeight+50);
        if(p) p.scrollTop = 0;
    }""")
    page.wait_for_timeout(500)
    box_in = page.locator("input[placeholder*='이름으로 찾기']")
    if box_in.count():
        box_in.first.fill('의성군 금성')
        page.wait_for_timeout(900)
        hit = page.locator("button:has-text('금성면')")
        if hit.count():
            hit.first.click()
            page.wait_for_timeout(3000)
            settle(page, 1500)
            snap(page, '14_region_pick',
                 '지역 찾기: 읍·면·동 검색 → SGIS 행정동 경계를 파랑으로 강조하고 화면 이동 '
                 '(경북 의성군 금성면 — 2025년 대형산불 발화지)')

    # ⑥ 분석 에이전트 — 공간질의
    ta = page.locator("textarea, input[placeholder*='궁금한']").last
    if ta.count():
        ta.fill('의성군 상황은 어때?')
        page.wait_for_timeout(400)
        ta.press('Enter')
        # 고정 대기는 위험하다. API 예산이 25초라 20초에 찍으면 "분석 중"이 걸린다.
        # 로딩 표시가 사라질 때까지 기다린다.
        try:
            page.wait_for_function(
                "() => !document.body.innerText.includes('분석 중')", timeout=60_000)
        except Exception:
            print('  · 에이전트 응답 대기 시간 초과 — 그 상태로 찍는다')
        page.wait_for_timeout(1200)
        chat = page.locator("div.glass:has-text('분석 에이전트')").last
        cb = chat.bounding_box()
        if cb:
            snap(page, '15_agent',
                 '분석 에이전트: 화면 밖 지역도 SGIS 격자 집계로 직접 답한다',
                 clip={'x': cb['x'], 'y': cb['y'], 'width': cb['width'], 'height': cb['height']})

    # ⑦ 전 기간 737일 모드
    tab = page.locator("button:has-text('전 기간')").first
    if tab.count():
        tab.click()
        page.wait_for_timeout(3500)
        settle(page, 1500)
        snap(page, '16_timeline',
             '전 기간 737일: 매일 산출한 위험등급 띠(하단)와 그날의 전국 위험지도. '
             '파란 눈금이 시간대별 예측이 있는 날')

    br.close()

print(f'\n캡처 {len(shots)}장 → {OUT}')
for n, d in shots:
    print(f'  {n}: {d}')
