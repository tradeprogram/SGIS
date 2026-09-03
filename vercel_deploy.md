# Vercel 배포 절차

## 1. 프로젝트 임포트
1. https://vercel.com/new 에서 `tradeprogram/SGIS` 선택
2. **Root Directory 를 `web` 으로 지정** (이게 핵심 — 저장소 루트에는 Next.js 앱이 없다)
3. Framework Preset 은 Next.js 로 자동 인식된다. Build/Output 설정은 기본값 그대로 둔다.

## 2. 환경변수 (Settings → Environment Variables)
| Key | Value | 적용 환경 |
|---|---|---|
| `GEMINI_API_KEY` | 발급받은 키 | Production, Preview, Development |
| `GEMINI_MODEL` | `gemini-3.6-flash` | 동일 |

키를 넣지 않아도 배포는 성공한다. 채팅은 화면 값만 안내하는 폴백으로 동작한다.

## 3. 배포 후 확인
- `/` 접속 → 741일 타임라인과 지도가 뜨는지
- 우측 채팅에 질문 → 응답 하단에 "GEMINI_API_KEY 미설정" 경고가 없으면 키가 정상
- 실패 시 Vercel → Functions 로그에서 `/api/chat` 의 `detail` 확인

## 4. 알아둘 제약
- **정적 자산 77MB / 1,167파일** (일별 PNG 21MB + 사례일 상세 50MB).
  Hobby 플랜에서 배포 용량이나 파일 수 제한에 걸리면 줄일 수 있는 순서:
  1. 사례일을 25일 → 10일로 축소 (`52_select_case_days.py` 의 연도별 개수)
  2. 사례일 시간대를 13개 → 7개로 (`53_build_multiday_assets.py` 의 `HOURS`)
  3. 일별 PNG 다운스케일 4 → 6 (`51_daily_scan_full_period.py` 의 `PNG_DOWNSCALE`)
- `/api/chat` 은 서버리스 함수라 정적 배포(`output: 'export'`)로 바꾸면 안 된다.
- 함수 타임아웃은 30초로 지정해 뒀다 (`src/pages/api/chat.ts` 의 `config`).
