/**
 * /api/chat — 분석 보조 에이전트.
 *
 * policymaps(api/chat.py + viz/ai_core.py)의 구조를 그대로 옮겼다.
 *   요청  { message, mode, context, history }
 *   응답  { answer, model?, suggestions, error?, detail? }
 *
 * policymaps 를 따른 점
 *   - Gemini 를 쓰고 GEMINI_API_KEY / GOOGLE_API_KEY 를 순서대로 읽는다
 *     (같은 키를 재사용할 수 있게 환경변수 이름을 맞췄다)
 *   - 시스템 프롬프트·질문·화면 컨텍스트·최근 대화를 하나의 JSON 으로 묶어 보낸다
 *   - 실패해도 200 으로 응답하고 폴백 답변을 준다. 채팅창이 죽지 않는 게 중요하다.
 *
 * 다른 점
 *   - 이 저장소는 Next.js 라 Python 서버리스 대신 API Route 로 구현했다.
 *   - 화면 컨텍스트가 정책 그래프가 아니라 "선택한 날짜의 위험도·노출·실제 발화"다.
 */

import type { NextApiRequest, NextApiResponse } from "next";
import { SYSTEM_PROMPT, localAnswer, suggestions } from "../../lib/prompt";

const BASE = "https://generativelanguage.googleapis.com/v1beta";
const MODEL = process.env.GEMINI_MODEL || "gemini-3.6-flash";
// 기본 모델이 과부하(503 UNAVAILABLE)일 때 순서대로 시도할 대체 모델.
// 실제로 gemini-3.6-flash 가 "high demand" 로 거절하는 일이 있었다.
const FALLBACK_MODELS = (process.env.GEMINI_FALLBACK_MODELS ?? "gemini-3.5-flash,gemini-flash-latest")
  .split(",")
  .map((m) => m.trim())
  .filter(Boolean);
const MAX_MESSAGE = 3000;
const MAX_HISTORY = 8;
// maxDuration(30초)보다 넉넉히 짧게. 우리가 먼저 끊어야 원인을 알려줄 수 있다.
const CALL_TIMEOUT_MS = 20000;   // 전체 예산
const ATTEMPT_TIMEOUT_MS = 9000; // 한 번의 호출
// 다시 걸어 볼 가치가 있는 상태코드. 503 은 모델 혼잡, 429 는 레이트리밋.
const RETRYABLE = new Set([429, 500, 502, 503, 504]);

// Vercel 기본 함수 타임아웃은 10초다. Gemini 응답이 그보다 오래 걸리는 경우가
// 있어 늘려 둔다. Hobby 플랜 상한은 60초다.
export const config = { maxDuration: 30 };

type Body = {
  message?: string;
  mode?: string;
  context?: unknown;
  history?: { role: string; text: string }[];
};

function apiKey(): string | null {
  return process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || null;
}

/** 이 키로 실제 쓸 수 있는 모델 목록. 모델명이 맞는지 확인하는 용도. */
async function listModels(key: string): Promise<string[]> {
  const r = await fetch(`${BASE}/models?key=${key}&pageSize=200`);
  if (!r.ok) throw new Error(`ListModels ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  return (j?.models ?? [])
    .filter((m: any) => (m?.supportedGenerationMethods ?? []).includes("generateContent"))
    .map((m: any) => String(m?.name ?? "").replace(/^models\//, ""));
}

type Attempt = { model: string; ok: boolean; status?: number; note?: string };

/** 한 모델에 한 번 호출. 재시도 판단은 호출한 쪽에서 한다. */
async function once(key: string, model: string, payload: unknown, budgetMs: number) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), Math.min(ATTEMPT_TIMEOUT_MS, budgetMs));
  try {
    const r = await fetch(`${BASE}/models/${model}:generateContent?key=${key}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      signal: ac.signal,
      body: JSON.stringify({
        contents: [{ role: "user", parts: [{ text: JSON.stringify(payload, null, 1) }] }],
        generationConfig: { temperature: 0.3, maxOutputTokens: 1200 },
      }),
    });
    if (!r.ok) return { status: r.status, body: (await r.text()).slice(0, 200) };
    const j = await r.json();
    const text =
      j?.candidates?.[0]?.content?.parts?.map((p: any) => p?.text ?? "").join("") ?? "";
    return text.trim() ? { text: text.trim() } : { status: 200, body: "빈 응답" };
  } catch (e: any) {
    return { status: e?.name === "AbortError" ? 504 : 0, body: String(e?.message ?? e).slice(0, 200) };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 기본 모델 → 대체 모델 순으로 시도한다.
 *
 * 실제로 gemini-3.6-flash 가 503 UNAVAILABLE("high demand")로 거절하는 일이
 * 있었고, 거절 응답이 오는 데만 10초가 걸렸다. 모델 하나에 매달리면 사용자는
 * 매번 폴백 답변만 보게 된다. 재시도 가능한 상태코드면 짧게 한 번 더 걸어 보고,
 * 그래도 안 되면 다음 모델로 넘어간다. 전체는 CALL_TIMEOUT_MS 예산 안에서만.
 */
async function callGemini(key: string, body: Body) {
  // policymaps 와 동일하게 지시·질문·컨텍스트를 한 덩어리 JSON 으로 넘긴다.
  // 모델이 화면 밖 지식으로 빠지지 않게 "여기 있는 값만 쓰라"고 못박는다.
  const payload = {
    instruction: SYSTEM_PROMPT,
    user_question: (body.message || "").slice(0, MAX_MESSAGE),
    current_view: body.mode || "timeline",
    screen_context: body.context ?? null,
    recent_history: (body.history || []).slice(-MAX_HISTORY),
    output_format:
      "한국어 대화체 3~6문단, 400~900자. 마크다운 헤더·표 금지. " +
      "screen_context 에 없는 수치는 지어내지 말 것.",
  };

  const deadline = Date.now() + CALL_TIMEOUT_MS;
  const trace: Attempt[] = [];

  for (const model of [MODEL, ...FALLBACK_MODELS]) {
    for (let tryNo = 0; tryNo < 2; tryNo++) {
      const left = deadline - Date.now();
      if (left < 1500) {
        trace.push({ model, ok: false, note: "시간 예산 소진" });
        throw Object.assign(new Error("남은 시간 안에 응답을 받지 못했습니다"), { trace });
      }
      const r: any = await once(key, model, payload, left);
      if (r.text) {
        trace.push({ model, ok: true });
        return { text: r.text as string, model, trace };
      }
      trace.push({ model, ok: false, status: r.status, note: r.body });
      if (!RETRYABLE.has(r.status)) break;   // 재시도 무의미 — 다음 모델로
      if (tryNo === 0) await new Promise((res) => setTimeout(res, 600));
    }
  }
  const last = trace[trace.length - 1];
  throw Object.assign(
    new Error(`모든 모델 실패 (마지막: ${last?.model} ${last?.status ?? ""} ${last?.note ?? ""})`),
    { trace }
  );
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  // GET /api/chat?diag=1 — 배포된 환경에서 무엇이 잘못됐는지 바로 본다.
  // 키 자체는 절대 내보내지 않고, 존재 여부와 사용 가능한 모델명만 돌려준다.
  if (req.method === "GET") {
    const key = apiKey();
    const out: Record<string, unknown> = {
      hasKey: !!key,
      keySource: process.env.GEMINI_API_KEY ? "GEMINI_API_KEY" : process.env.GOOGLE_API_KEY ? "GOOGLE_API_KEY" : null,
      configuredModel: MODEL,
      callTimeoutMs: CALL_TIMEOUT_MS,
    };
    if (key && req.query.diag) {
      try {
        const models = await listModels(key);
        out.modelIsAvailable = models.includes(MODEL);
        out.availableModels = models;
      } catch (e: any) {
        out.listModelsError = String(e?.message ?? e).slice(0, 300);
      }
    }
    res.status(200).json(out);
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "method_not_allowed" });
    return;
  }
  const body: Body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
  const sug = suggestions(body.context);
  const key = apiKey();

  if (!key) {
    res.status(200).json({
      answer: localAnswer(body.message || "", body.context),
      suggestions: sug,
      error: "no_api_key",
    });
    return;
  }

  try {
    const g = await callGemini(key, body);
    res.status(200).json({
      answer: g.text,
      model: g.model,
      // 기본 모델이 아니라 대체 모델로 답한 경우 화면에 알린다.
      fallbackModel: g.model !== MODEL ? MODEL : undefined,
      suggestions: sug,
    });
  } catch (e: any) {
    // 채팅창을 죽이지 않는다. 폴백 답변과 함께 원인을 같이 내려보낸다.
    const overloaded = (e?.trace ?? []).some((t: any) => t.status === 503);
    res.status(200).json({
      answer: localAnswer(body.message || "", body.context),
      suggestions: sug,
      error: overloaded ? "llm_overloaded" : "llm_failed",
      detail: String(e?.message ?? e).slice(0, 300),
      trace: e?.trace,
    });
  }
}
