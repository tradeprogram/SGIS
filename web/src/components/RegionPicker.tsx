import { useEffect, useMemo, useRef, useState } from "react";

/** 64번이 만든 adm_index.json 구조 */
export type Dong = { cd: string; nm: string; c: [number, number]; b: [number, number, number, number] };
export type AdmIndex = Record<string, Record<string, Dong[]>>;
/** 지도에 넘길 선택 결과 — 경계 강조와 화면 이동에 필요한 것만 */
export type Picked = { cd: string; nm: string; sido: string; sgg: string; b: Dong["b"] };

type Props = {
  picked: Picked | null;
  onPick: (p: Picked | null) => void;
};

const SIDO_SHORT: Record<string, string> = {
  서울특별시: "서울", 부산광역시: "부산", 대구광역시: "대구", 인천광역시: "인천",
  광주광역시: "광주", 대전광역시: "대전", 울산광역시: "울산", 세종특별자치시: "세종",
  경기도: "경기", 강원특별자치도: "강원", 충청북도: "충북", 충청남도: "충남",
  전북특별자치도: "전북", 전라남도: "전남", 경상북도: "경북", 경상남도: "경남",
  제주특별자치도: "제주",
};

export default function RegionPicker({ picked, onPick }: Props) {
  const [idx, setIdx] = useState<AdmIndex | null>(null);
  const [sido, setSido] = useState("");
  const [sgg, setSgg] = useState("");
  const [q, setQ] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let dead = false;
    fetch("/data/adm_index.json")
      .then((r) => r.json())
      .then((j: AdmIndex) => !dead && setIdx(j))
      .catch(() => {});
    return () => {
      dead = true;
    };
  }, []);

  // 선택이 바깥에서 바뀌면(예: 초기화) 드롭다운도 따라간다
  useEffect(() => {
    if (picked) {
      setSido(picked.sido);
      setSgg(picked.sgg);
    }
  }, [picked]);

  // 검색은 전체 3,559개를 훑는다. 매 입력마다 평탄화하면 낭비라 한 번만 만든다.
  const flat = useMemo(() => {
    if (!idx) return [];
    const out: Picked[] = [];
    for (const sd of Object.keys(idx))
      for (const sg of Object.keys(idx[sd]))
        for (const d of idx[sd][sg]) out.push({ cd: d.cd, nm: d.nm, sido: sd, sgg: sg, b: d.b });
    return out;
  }, [idx]);

  const hits = useMemo(() => {
    // "의성군 금성" 처럼 상위 지명과 함께 치는 게 자연스럽다. 낱말로 쪼개
    // 전부 걸리는 것만 남긴다. 한 필드만 보면 이런 입력이 통째로 빗나간다.
    const toks = q.trim().split(/\s+/).filter(Boolean);
    if (!toks.length) return [];
    return flat
      .filter((d) => {
        const hay = `${d.nm} ${d.sgg} ${d.sido} ${SIDO_SHORT[d.sido] ?? ""}`;
        return toks.every((t) => hay.includes(t));
      })
      .slice(0, 60);
  }, [q, flat]);

  if (!idx) return <div className="text-[10px] text-slate-500">행정경계 불러오는 중…</div>;

  const sidos = Object.keys(idx);
  const sggs = sido ? Object.keys(idx[sido]) : [];
  const dongs = sido && sgg ? idx[sido][sgg] : [];

  const sel = (cls = "") =>
    "w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-[11px] text-white " +
    "outline-none focus:border-sky-400/60 " + cls;

  return (
    <div ref={boxRef}>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="동·시군구 이름으로 찾기"
        className={sel("mb-1.5")}
      />

      {hits.length > 0 ? (
        <div className="scroll-thin mb-1.5 max-h-[190px] overflow-y-auto rounded-lg border border-white/10 bg-black/30">
          {hits.map((d) => (
            <button
              key={d.cd}
              onClick={() => {
                onPick(d);
                setQ("");
              }}
              className="block w-full px-2 py-1.5 text-left text-[11px] text-slate-200 hover:bg-sky-400/15"
            >
              <span className="font-medium text-white">{d.nm}</span>
              <span className="ml-1.5 text-[10px] text-slate-400">
                {SIDO_SHORT[d.sido] ?? d.sido} {d.sgg}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <>
          <select
            value={sido}
            onChange={(e) => {
              setSido(e.target.value);
              setSgg("");
            }}
            className={sel("mb-1.5")}
          >
            <option value="">시·도 선택</option>
            {sidos.map((s) => (
              <option key={s} value={s} className="bg-slate-900">
                {s}
              </option>
            ))}
          </select>

          <select
            value={sgg}
            onChange={(e) => setSgg(e.target.value)}
            disabled={!sido}
            className={sel("mb-1.5 disabled:opacity-40")}
          >
            <option value="">시·군·구 선택</option>
            {sggs.map((s) => (
              <option key={s} value={s} className="bg-slate-900">
                {s}
              </option>
            ))}
          </select>

          <select
            value={picked?.cd ?? ""}
            onChange={(e) => {
              const d = dongs.find((x) => x.cd === e.target.value);
              onPick(d ? { cd: d.cd, nm: d.nm, sido, sgg, b: d.b } : null);
            }}
            disabled={!sgg}
            className={sel("disabled:opacity-40")}
          >
            <option value="">읍·면·동 선택</option>
            {dongs.map((d) => (
              <option key={d.cd} value={d.cd} className="bg-slate-900">
                {d.nm}
              </option>
            ))}
          </select>
        </>
      )}

      {picked && (
        <div className="mt-2 flex items-center gap-2 rounded-lg border border-sky-400/40 bg-sky-400/10 px-2 py-1.5">
          <span className="h-3 w-3 shrink-0 rounded-sm border-2 border-sky-400" />
          <div className="min-w-0">
            <div className="truncate text-[11px] font-semibold text-white">{picked.nm}</div>
            <div className="truncate text-[10px] text-slate-400">
              {SIDO_SHORT[picked.sido] ?? picked.sido} {picked.sgg}
            </div>
          </div>
          <button
            onClick={() => onPick(null)}
            className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-white/10"
          >
            해제
          </button>
        </div>
      )}
    </div>
  );
}
