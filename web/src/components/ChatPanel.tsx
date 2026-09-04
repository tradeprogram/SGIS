/**
 * 우측 고정 에이전트 패널.
 *
 * 지도를 보면서 바로 물을 수 있어야 의사결정 보조가 된다. 그래서 열고 닫는
 * 팝업이 아니라 화면에 박아 둔 고정 패널이다. 접히지 않는다 — 닫는 순간
 * "부가 기능"이 되어 버리고, 담당자가 지도만 보다 끝난다.
 *
 * 컨텍스트는 부모가 만들어 넘긴다. 사용자가 보고 있는 날짜·등급·노출·실제 발화가
 * 그대로 모델에 들어가므로, "이 날"이라고만 물어도 답이 된다.
 */

import { useEffect, useRef, useState } from "react";

type Msg = { role: "user" | "agent"; text: string; error?: string; usedModel?: string };

export type ChatContext = {
  mode: "timeline" | "detail";
  day: {
    date: string;
    level: string | null;
    /** 이미 뒤집은 값. 작을수록 위험한 날 = "상위 N%". */
    topPct: number | null;
    e1: number;
    e5: number;
    wui: number;
    n: number;
    ha: number;
    fires: { loc: string; hh: number; ha: number }[];
  } | null;
  hour?: number;
  priority?: {
    rank: number; name: string; topPct: number;
    /** pop = 상주인구(야간), popDay = 순위를 정하는 주간 보정 인구 */
    pop: number; popDay: number; popOld: number; avgAge: number | null;
  }[];
  cell?: Record<string, unknown> | null;
};

const GREETING =
  "산불 발화예측·우선대응 통합지도입니다. 화면에 보이는 날짜의 위험도·노출인구·실제 발화 기록을 " +
  "보면서 답해 드립니다.\n\n한 가지 먼저 말씀드리면, 이 지도의 위험도는 발생확률이 아니라 " +
  "전국 403,385격자 안에서의 상대 순위입니다. 무엇이든 물어보세요.";

export default function ChatPanel({ ctx }: { ctx: ChatContext }) {
  const [msgs, setMsgs] = useState<Msg[]>([{ role: "agent", text: GREETING }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [chips, setChips] = useState<string[]>([
    "오늘은 왜 이 등급인가요?",
    "이 위험도를 확률로 봐도 되나요?",
    "대응 우선지역은 어떻게 정해졌나요?",
  ]);
  const listRef = useRef<HTMLDivElement>(null);
  const lastQ = useRef("");
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  // scrollIntoView 를 쓰면 안 된다. 이 앱은 min-width 때문에 가로 스크롤이
  // 생기는데, 화면 밖의 채팅 패널을 보이게 하려고 브라우저가 페이지 전체를
  // 가로로 밀어 버린다. 접속하자마자 지도에서 밀려나는 일이 생긴다.
  // 목록 컨테이너만 직접 내린다.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, busy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    lastQ.current = q;
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          message: q,
          mode: ctxRef.current.mode,
          context: ctxRef.current,
          // 직전 대화만 넘긴다. 길어지면 화면 컨텍스트가 묻힌다.
          history: msgs.slice(-8).map((m) => ({ role: m.role, text: m.text })),
        }),
      });
      // /api/chat 이 배포되지 않으면 Vercel 이 text/plain 404 를 준다. 그대로
      // r.json() 하면 파싱 에러가 나서 "네트워크 문제"로 오진하게 된다.
      // 상태코드와 본문을 먼저 보고 원인을 정확히 알려준다.
      const raw = await r.text();
      if (!r.ok) {
        setMsgs((m) => [
          ...m,
          {
            role: "agent",
            text:
              r.status === 404
                ? "서버의 분석 API(/api/chat)를 찾을 수 없습니다. 배포에 API 라우트가 포함되지 않은 상태입니다."
                : `서버가 ${r.status} 오류를 반환했습니다.`,
            error: `http_${r.status}`,
          },
        ]);
        return;
      }
      let j: any;
      try {
        j = JSON.parse(raw);
      } catch {
        setMsgs((m) => [
          ...m,
          { role: "agent", text: "서버 응답을 해석하지 못했습니다.", error: "bad_response" },
        ]);
        return;
      }
      setMsgs((m) => [
        ...m,
        { role: "agent", text: j.answer ?? "응답이 비어 있습니다.", error: j.error, usedModel: j.fallbackModel ? j.model : undefined },
      ]);
      if (Array.isArray(j.suggestions) && j.suggestions.length) setChips(j.suggestions);
    } catch {
      setMsgs((m) => [
        ...m,
        { role: "agent", text: "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.", error: "network" },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pointer-events-none absolute bottom-3 right-3 top-[4.75rem] z-20 w-[360px]">
      <div className="glass glass-live hud pointer-events-auto flex h-full flex-col overflow-hidden">
        <div className="flex items-center gap-2 border-b border-sky-400/15 bg-sky-400/[0.04] px-3 py-2.5">
          <span className="dot-live shrink-0" />
          <div>
            <div className="text-[12px] font-semibold tracking-wide text-white">분석 에이전트</div>
            <div className="text-[9px] uppercase tracking-[0.18em] text-sky-300/60">
              decision support
            </div>
          </div>
          <div className="tnum ml-auto text-right">
            <div className="glow text-[11px] font-medium text-sky-200">
              {ctx.day ? ctx.day.date : "—"}
            </div>
            <div className="text-[9px] text-slate-500">
              {ctx.mode === "detail" ? "시간대별 상세" : "전 기간"}
            </div>
          </div>
        </div>

        <div ref={listRef} className="scroll-thin flex-1 space-y-2.5 overflow-y-auto px-3 py-3">
          {msgs.map((m, i) => (
            <div
              key={i}
              className={
                "whitespace-pre-wrap rounded-lg px-2.5 py-2 text-[11.5px] leading-relaxed " +
                (m.role === "user"
                  ? "ml-6 border border-sky-400/25 bg-sky-400/[0.12] text-white"
                  : "mr-2 border border-white/[0.07] bg-white/[0.035] text-slate-200")
              }
            >
              {m.text}
              {m.error === "no_api_key" && (
                <div className="mt-1.5 text-[10px] text-amber-300/80">
                  GEMINI_API_KEY 미설정 — 화면 값만 안내 중입니다.
                </div>
              )}
              {m.error === "llm_failed" && (
                <div className="mt-1.5 text-[10px] text-amber-300/80">
                  AI 호출 실패 — 화면 값으로 대체했습니다.
                </div>
              )}
              {m.error === "llm_slow" && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-amber-300/80">
                  <span>AI 응답이 제한 시간 안에 오지 않아 화면 값으로 대체했습니다.</span>
                  <button
                    onClick={() => send(lastQ.current)}
                    disabled={busy}
                    className="pill border border-amber-300/30 bg-amber-300/10 px-2 py-0.5 text-[10px] text-amber-200 hover:bg-amber-300/20 disabled:opacity-40"
                  >
                    다시 시도
                  </button>
                </div>
              )}
              {m.error === "llm_overloaded" && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-amber-300/80">
                  <span>모델이 일시적으로 혼잡합니다(구글 측 503). 화면 값으로 대체했습니다.</span>
                  <button
                    onClick={() => send(lastQ.current)}
                    disabled={busy}
                    className="pill border border-amber-300/30 bg-amber-300/10 px-2 py-0.5 text-[10px] text-amber-200 hover:bg-amber-300/20 disabled:opacity-40"
                  >
                    다시 시도
                  </button>
                </div>
              )}
              {m.usedModel && (
                <div className="mt-1.5 text-[10px] text-sky-300/60">
                  기본 모델 혼잡으로 {m.usedModel} 로 답했습니다.
                </div>
              )}
              {m.error?.startsWith("http_") && (
                <div className="mt-1.5 text-[10px] text-rose-300/80">
                  Vercel 프로젝트 설정에서 Framework Preset 을 Next.js 로 두고
                  Build Command·Output Directory Override 를 끈 뒤 재배포해야 합니다.
                </div>
              )}
            </div>
          ))}
          {busy && (
            <div className="thinking mr-2 flex items-center gap-2 rounded-lg border border-sky-400/15 bg-sky-400/[0.05] px-2.5 py-2 text-[11.5px] text-sky-200/80">
              <span className="dot-live shrink-0" />
              분석 중<span>.</span>
              <span>.</span>
              <span>.</span>
            </div>
          )}
        </div>

        <div className="border-t border-sky-400/15 bg-sky-400/[0.03] px-3 py-2.5">
          <div className="mb-2 flex flex-wrap gap-1">
            {chips.map((c) => (
              <button
                key={c}
                onClick={() => send(c)}
                disabled={busy}
                className="pill border border-sky-400/20 bg-sky-400/[0.07] px-2 py-1 text-[10px] text-sky-100/80 transition hover:border-sky-400/50 hover:bg-sky-400/20 disabled:opacity-40"
              >
                {c}
              </button>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // 한글 IME 조합 중의 Enter 는 글자를 확정하는 키다. 이걸 전송으로
                // 받으면 "안녕하세" 같은 미완성 문장이 그대로 날아간다.
                // isComposing 인 동안에는 무시한다.
                if (e.key !== "Enter" || e.shiftKey) return;
                if ((e.nativeEvent as any).isComposing || e.keyCode === 229) return;
                e.preventDefault();
                send(input);
              }}
              rows={1}
              placeholder="궁금한 점을 물어보세요…"
              className="scroll-thin max-h-24 flex-1 resize-none rounded-lg border border-sky-400/20 bg-[#070d1f]/70 px-2.5 py-2 text-[11.5px] text-white outline-none transition placeholder:text-slate-500 focus:border-sky-400/60 focus:shadow-[0_0_0_3px_rgba(56,189,248,0.12)]"
            />
            <button
              onClick={() => send(input)}
              disabled={busy || !input.trim()}
              className="pill flex h-8 w-8 shrink-0 items-center justify-center bg-accent/70 text-white hover:bg-accent disabled:opacity-30"
              aria-label="보내기"
            >
              ➤
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
