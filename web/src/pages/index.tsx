import { useEffect, useMemo, useRef, useState } from "react";

type MlMap = any;

const nf = (n: number, d = 0) =>
  n.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });

export default function Home() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [values, setValues] = useState<CellValues>({});
  const [priority, setPriority] = useState<Record<string, PriorityItem[]>>({});
  const [fires, setFires] = useState<Fire[]>([]);
  const [hour, setHour] = useState(12);
  const [sel, setSel] = useState<Selected | null>(null);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState<"risk" | "priority">("priority");
  const mapRef = useRef<MlMap | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/data/meta.json").then((r) => r.json()),
      fetch("/data/cells_values.json").then((r) => r.json()),
      fetch("/data/priority.json").then((r) => r.json()),
      fetch("/data/fires.json").then((r) => r.json()),
    ]).then(([m, v, p, f]) => {
      setMeta(m);
      setValues(v);
      setPriority(p);
      setFires(f);
      setHour(m.hours.includes(12) ? 12 : m.hours[0]);
    });
  }, []);

  // 재생
  useEffect(() => {
    if (!playing || !meta) return;
    const id = setInterval(() => {
      setHour((h) => {
        const i = meta.hours.indexOf(h);
        return meta.hours[(i + 1) % meta.hours.length];
      });
    }, 1100);
    return () => clearInterval(id);
  }, [playing, meta]);

  const s = meta?.summary[String(hour)];
  const top = priority[String(hour)] ?? [];
  const hourFires = useMemo(
    () => fires.filter((f) => f.hh >= hour + 1 && f.hh <= hour + 3),
    [fires, hour]
  );

  if (!meta) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">
        데이터 불러오는 중…
      </div>
    );
  }

  const idx = meta.hours.indexOf(hour);

  return (
    <main className="relative h-full w-full overflow-hidden bg-ink">
      <FireMap
        meta={meta}
        values={values}
        fires={fires}
        hour={hour}
        onSelect={setSel}
        onReady={(m) => (mapRef.current = m)}
      />

      {/* ── 상단 바 ─────────────────────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start gap-2 p-3">
        <div className="glass pointer-events-auto flex items-center gap-2 px-3 py-2">
          <span className="text-base">🔥</span>
          <div className="leading-tight">
            <div className="text-[13px] font-semibold text-white">산불先지도</div>
            <div className="text-[10px] text-slate-400">AI 발화예측 × SGIS 공간통계</div>
          </div>
        </div>

        <div className="glass pointer-events-auto flex gap-1.5 p-1.5">
          {([["priority", "대응 우선순위"], ["risk", "위험 예측"]] as const).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`pill ${
                tab === k ? "bg-accent/70 text-white" : "text-slate-300 hover:bg-white/10"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="glass pointer-events-auto ml-auto px-3 py-2 text-right">
          <div className="text-[10px] text-slate-400">사례 재현</div>
          <div className="tnum text-[13px] font-semibold text-white">
            {meta.date} {String(hour).padStart(2, "0")}:00 KST
          </div>
        </div>
      </div>

      {/* ── 좌측: 우선지역 / 요약 ───────────────────────────────── */}
      <div className="pointer-events-none absolute left-3 top-24 z-10 w-[292px]">
        <div className="glass pointer-events-auto flex max-h-[calc(100vh-15rem)] flex-col p-3">
          {tab === "priority" ? (
            <>
              <div className="mb-1 text-[12px] font-semibold text-white">대응 우선지역 Top 10</div>
              <div className="mb-2 text-[10px] leading-relaxed text-slate-400">
                위험 백분위와 SGIS 노출인구 백분위의 평균 순.
                산림 30% 이상·인구 10명 이상 격자(WUI)로 한정.
              </div>
              <div className="scroll-thin -mr-1 overflow-y-auto pr-1">
                {top.map((p, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      mapRef.current?.flyTo({ center: [p.lon, p.lat], zoom: 11, duration: 900 });
                      setSel({
                        i: p.i, pop: p.pop, hh_: 0, ho: 0, lowq: false,
                        top: p.top, score: p.score, lon: p.lon, lat: p.lat,
                      });
                    }}
                    className="mb-1 flex w-full items-center gap-2 rounded-lg border border-white/5 bg-white/[0.03] px-2 py-1.5 text-left transition hover:bg-white/10"
                  >
                    <span
                      className={`tnum flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] font-bold ${
                        i < 3 ? "bg-red-500/80 text-white" : "bg-white/10 text-slate-300"
                      }`}
                    >
                      {i + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="tnum block text-[11px] font-medium text-white">
                        위험 상위 {p.top.toFixed(2)}%
                      </span>
                      <span className="tnum block text-[10px] text-slate-400">
                        노출 {nf(p.pop)}명 · 산림 {(p.forest * 100).toFixed(0)}%
                      </span>
                    </span>
                    <span className="tnum shrink-0 text-[11px] font-semibold text-accent">
                      {p.score.toFixed(1)}
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="mb-2 text-[12px] font-semibold text-white">이 시각 전국 요약</div>
              <Row label="위험 상위 1% 격자 노출인구" value={`${nf(s?.top1_pop ?? 0)}명`} />
              <Row label="위험 상위 5% 격자 노출인구" value={`${nf(s?.top5_pop ?? 0)}명`} />
              <Row label="WUI ∩ 상위 5% 격자" value={`${nf(s?.wui_top5_cells ?? 0)}개`} />
              <Row label="Top 10 합계 노출인구" value={`${nf(s?.top10_pop ?? 0)}명`} />
              <div className="mt-2 border-t border-white/10 pt-2">
                <div className="mb-1.5 text-[11px] font-semibold text-slate-300">범례</div>
                {meta.legend.map((l) => (
                  <div key={l.label} className="mb-1 flex items-center gap-2">
                    <span
                      className="h-2.5 w-4 rounded-sm"
                      style={{ background: l.color }}
                    />
                    <span className="text-[10px] text-slate-400">{l.label}</span>
                  </div>
                ))}
                <div className="mt-1 flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full border-2 border-slate-900 bg-white" />
                  <span className="text-[10px] text-slate-400">실제 발화 지점</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ── 우측: 선택 격자 분석 ───────────────────────────────── */}
      {sel && (
        <div className="pointer-events-none absolute right-3 top-24 z-10 w-[264px]">
          <div className="glass pointer-events-auto p-3">
            <div className="mb-2 flex items-start justify-between">
              <div className="text-[12px] font-semibold text-white">선택 격자 분석</div>
              <button
                onClick={() => setSel(null)}
                className="text-[11px] text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <div className="mb-3 rounded-lg bg-white/[0.04] p-2.5">
              <div className="text-[10px] text-slate-400">전국 상대 위험도</div>
              <div className="tnum text-[22px] font-bold leading-tight text-white">
                상위 {sel.top != null ? sel.top.toFixed(2) : "—"}%
              </div>
              <div className="mt-0.5 text-[10px] text-slate-500">
                t+1h 기준 · 확률 아님
              </div>
            </div>

            <div className="mb-1.5 text-[11px] font-semibold text-slate-300">
              SGIS 노출 (2024 · 500m 격자)
            </div>
            <Row label="인구" value={`${nf(sel.pop, 0)}명`} />
            <Row label="가구" value={sel.hh_ ? `${nf(sel.hh_)}가구` : "—"} />
            <Row label="주택" value={sel.ho ? `${nf(sel.ho)}호` : "—"} />

            {sel.lowq && (
              <div className="mt-2 rounded-lg border border-amber-400/25 bg-amber-400/10 p-2 text-[10px] leading-relaxed text-amber-200">
                이 격자의 SGIS 통계는 <b>비공개 처리 구간(0·5·8)</b>으로만 구성돼 있어
                실제 값과 오차가 클 수 있습니다.
              </div>
            )}

            <div className="tnum mt-2 text-[10px] text-slate-500">
              {sel.lat.toFixed(4)}°N {sel.lon.toFixed(4)}°E
            </div>
          </div>
        </div>
      )}

      {/* ── 하단: 시간 슬라이더 ───────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 p-3">
        <div className="glass pointer-events-auto mx-auto max-w-3xl px-4 py-3">
          <div className="mb-1.5 flex items-center gap-3">
            <button
              onClick={() => setPlaying((p) => !p)}
              className="pill flex h-7 w-7 items-center justify-center bg-accent/70 text-white"
            >
              {playing ? "❚❚" : "▶"}
            </button>
            <div className="text-[11px] text-slate-300">
              시간을 움직이면 <b className="text-white">위험지도</b>와{" "}
              <b className="text-white">SGIS 노출인구</b>가 함께 갱신됩니다
            </div>
            <div className="tnum ml-auto text-[11px] text-slate-400">
              예측 대상 {String(hour + 1).padStart(2, "0")}~{String(hour + 3).padStart(2, "0")}시
              {hourFires.length > 0 && (
                <span className="ml-2 rounded bg-white/15 px-1.5 py-0.5 text-white">
                  이 구간 실제 발화 {hourFires.length}건
                </span>
              )}
            </div>
          </div>

          <input
            type="range"
            className="tick w-full"
            min={0}
            max={meta.hours.length - 1}
            step={1}
            value={idx}
            onChange={(e) => setHour(meta.hours[Number(e.target.value)])}
          />
          <div className="tnum mt-0.5 flex justify-between text-[10px] text-slate-500">
            {meta.hours.map((h) => (
              <span key={h} className={h === hour ? "font-bold text-accent" : ""}>
                {String(h).padStart(2, "0")}
              </span>
            ))}
          </div>
        </div>
      </div>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between py-0.5">
      <span className="text-[11px] text-slate-400">{label}</span>
      <span className="tnum text-[12px] font-medium text-white">{value}</span>
    </div>
  );
}

// ─────────────── 타입 ───────────────
type Meta = {
  date: string; hours: number[]; image_corners: [number, number][];
  top_pct_shown: number; vector_pct: number;
  legend: { label: string; color: string }[];
  summary: Record<string, { top1_pop: number; top5_pop: number; wui_top5_cells: number;
                            top10_pop: number; top10_haz: number | null; n_fire: number }>;
  note: string;
};
/** 시각별 셀 값 — i: cells.geojson feature id, top: 전국 위험 백분위(0=1위) */
type CellValues = Record<string, { i: number[]; top: number[]; score: (number | null)[] }>;
type PriorityItem = { i: number; lon: number; lat: number; top: number; score: number;
                      pop: number; forest: number };
type Fire = { lon: number; lat: number; hh: number; loc: string; ha: number; cells: number };
type CellProps = { i: number; pop: number; hh_: number; ho: number; lowq: boolean };
type Selected = CellProps & { top: number | null; score: number | null; lon: number; lat: number };

// ─────────────── 지도 ───────────────
/** maplibre-gl을 CDN에서 1회 로드한다.
 *  번들에 넣으면 Next dev(pages router)에서 async 청크가 컴파일되지 않아
 *  하이드레이션이 영구히 멈추는 문제가 있었다. CSS는 _document에서 링크한다. */
let mlPromise: Promise<any> | null = null;
function loadMapLibre(): Promise<any> {
  if ((window as any).maplibregl) return Promise.resolve((window as any).maplibregl);
  if (mlPromise) return mlPromise;
  mlPromise = new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js";
    el.async = true;
    el.onload = () => resolve((window as any).maplibregl);
    el.onerror = () => reject(new Error("maplibre-gl 로드 실패"));
    document.head.appendChild(el);
  });
  return mlPromise;
}

const STYLE = "https://tiles.openfreemap.org/styles/dark";

type Props = {
  meta: Meta;
  values: CellValues;
  fires: Fire[];
  hour: number;
  onSelect: (s: Selected | null) => void;
  onReady: (m: MlMap) => void;
};

function FireMap({ meta, values, fires, hour, onSelect, onReady }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const loaded = useRef(false);

  useEffect(() => {
    let dead = false;
    let m: MlMap | null = null;

    (async () => {
      const maplibregl = await loadMapLibre();
      if (dead || !box.current || map.current) return;

      m = new maplibregl.Map({
        container: box.current,
        style: STYLE,
        center: [127.9, 36.3],
        zoom: 6.4,
        minZoom: 5.5,
        maxZoom: 13,
      });
      map.current = m;
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

      m.on("load", () => {
        if (dead || !m) return;

        // 배경 위험 래스터 (EPSG:3857로 미리 워프해둔 PNG)
        m.addSource("hazard", {
          type: "image",
          url: `/data/hazard_${String(hour).padStart(2, "0")}.png`,
          coordinates: meta.image_corners,
        });
        m.addLayer({
          id: "hazard",
          type: "raster",
          source: "hazard",
          paint: { "raster-opacity": 0.85, "raster-resampling": "nearest" },
        });

        // 클릭 가능한 상위 위험 셀
        m.addSource("cells", { type: "geojson", data: "/data/cells.geojson" });
        m.addLayer({
          id: "cells-fill",
          type: "fill",
          source: "cells",
          paint: {
            "fill-color": [
              "interpolate", ["linear"], ["coalesce", ["feature-state", "top"], 99],
              0, "#ef4444", 1, "#fb923c", 3, "#facc15", 5, "#a3e635",
            ],
            // 완전 투명이면 클릭이 안 잡히므로 최소값을 둔다
            "fill-opacity": ["case", ["!=", ["feature-state", "top"], null], 0.28, 0.01],
          },
        });
        m.addLayer({
          id: "cells-line",
          type: "line",
          source: "cells",
          paint: {
            "line-color": "#38bdf8",
            "line-width": ["case", ["boolean", ["feature-state", "sel"], false], 2.4, 0],
          },
        });

        // 실제 발화점
        m.addSource("fires", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: fires.map((f, i) => ({
              type: "Feature" as const,
              id: i,
              geometry: { type: "Point" as const, coordinates: [f.lon, f.lat] },
              properties: { ...f },
            })),
          },
        });
        m.addLayer({
          id: "fires",
          type: "circle",
          source: "fires",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 4.5, 11, 9],
            "circle-color": "#ffffff",
            "circle-opacity": 0.95,
            "circle-stroke-width": 2,
            "circle-stroke-color": "#0f172a",
          },
        });

        m.on("click", "cells-fill", (e: any) => {
          const f = e.features?.[0];
          if (!f || !m) return;
          const p = f.properties as any;
          const st = m.getFeatureState({ source: "cells", id: f.id as number }) as any;
          onSelect({
            i: Number(p.i), pop: Number(p.pop), hh_: Number(p.hh_), ho: Number(p.ho),
            lowq: p.lowq === true || p.lowq === "true",
            top: st?.top ?? null, score: st?.score ?? null,
            lon: e.lngLat.lng, lat: e.lngLat.lat,
          });
        });
        m.on("mouseenter", "cells-fill", () => m && (m.getCanvas().style.cursor = "pointer"));
        m.on("mouseleave", "cells-fill", () => m && (m.getCanvas().style.cursor = ""));

        const pop = new maplibregl.Popup({ closeButton: false, offset: 12 });
        m.on("mouseenter", "fires", (e: any) => {
          const p = e.features?.[0]?.properties as any;
          if (!p || !m) return;
          m.getCanvas().style.cursor = "pointer";
          pop.setLngLat(e.lngLat)
            .setHTML(
              `<div style="font:12px Pretendard,sans-serif;color:#0f172a">
                 <b>실제 발화</b><br/>${p.loc}<br/>${String(p.hh).padStart(2, "0")}시 · ${p.ha}ha</div>`
            )
            .addTo(m);
        });
        m.on("mouseleave", "fires", () => {
          if (!m) return;
          m.getCanvas().style.cursor = "";
          pop.remove();
        });

        loaded.current = true;
        onReady(m);
        applyHour(m, values, hour);
      });
    })();

    return () => {
      dead = true;
      map.current?.remove();
      map.current = null;
      loaded.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 시각 변경 ─────────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current;
    if (!m || !loaded.current) return;
    const src = m.getSource("hazard") as any;
    src?.updateImage?.({ url: `/data/hazard_${String(hour).padStart(2, "0")}.png` });
    applyHour(m, values, hour);
  }, [hour, values]);

  return <div ref={box} className="absolute inset-0" />;
}

/** 해당 시각의 위험 백분위를 feature-state로 밀어넣는다. */
function applyHour(m: MlMap, values: CellValues, hour: number) {
  if (!m.getSource("cells")) return;
  m.removeFeatureState({ source: "cells" });
  const v = values[String(hour)];
  if (!v) return;
  for (let k = 0; k < v.i.length; k++) {
    m.setFeatureState({ source: "cells", id: v.i[k] }, { top: v.top[k], score: v.score[k] });
  }
}
