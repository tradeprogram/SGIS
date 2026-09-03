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

type Msg = { role: "user" | "agent"; text: string; error?: string };

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
  priority?: { rank: number; name: string; topPct: number; pop: number }[];
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
  const endRef = useRef<HTMLDivElement>(null);
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs, busy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
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
      const j = await r.json();
      setMsgs((m) => [...m, { role: "agent", text: j.answer ?? "응답이 비어 있습니다.", error: j.error }]);
      if (Array.isArray(j.suggestions) && j.suggestions.length) setChips(j.suggestions);
    } catch (e: any) {
      setMsgs((m) => [
        ...m,
        { role: "agent", text: "요청을 보내지 못했습니다. 네트워크를 확인해 주세요.", error: "network" },
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

        <div className="scroll-thin flex-1 space-y-2.5 overflow-y-auto px-3 py-3">
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
          <div ref={endRef} />
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
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
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
