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

  useEffect(() => {
    if (!playing || !meta) return;
    const id = setInterval(() => {
      setHour((h) => meta.hours[(meta.hours.indexOf(h) + 1) % meta.hours.length]);
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
  const lv = sel && sel.top != null ? levelOf(meta, sel.top) : null;

  return (
    <main className="relative h-full w-full overflow-hidden bg-ink">
      <FireMap
        meta={meta}
        values={values}
        fires={fires}
        hour={hour}
        onSelect={setSel}
        onReady={(m: MlMap) => (mapRef.current = m)}
      />

      {/* ── 헤더 ─────────────────────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center gap-3 p-3">
        <div className="glass pointer-events-auto flex items-center gap-2 px-3 py-2">
          <span className="text-lg">🔥</span>
          <div className="leading-tight">
            <div className="text-[14px] font-semibold text-white">산불先지도</div>
            <div className="text-[10px] text-slate-400">AI 발화예측 × SGIS 공간통계</div>
          </div>
        </div>
        <div className="glass pointer-events-auto ml-auto px-3 py-2 text-right">
          <div className="text-[10px] text-slate-400">사례 재현 · 신규발화 위험</div>
          <div className="tnum text-[14px] font-semibold text-white">
            {meta.date} <span className="text-accent">{String(hour).padStart(2, "0")}:00</span> KST
          </div>
        </div>
      </div>

      {/* ── 좌측 패널 ─────────────────────────────────────────── */}
      <div className="pointer-events-none absolute bottom-24 left-3 top-[4.75rem] z-10 w-[286px]">
        <div className="glass scroll-thin pointer-events-auto h-full overflow-y-auto p-3">
          <SectionTitle>위험 등급</SectionTitle>
          <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
            확률이 아니라 <b className="text-slate-300">전국 상대 순위</b>입니다. 재표본화 학습이라
            확률로 읽으면 안 됩니다.
          </div>
          {meta.levels.map((l, i) => (
            <div
              key={l.key}
              className="mb-1 flex items-center gap-2 rounded-lg px-2 py-1.5"
              style={{ background: l.color + "22", border: "1px solid " + l.color + "55" }}
            >
              <span className="h-3 w-3 shrink-0 rounded" style={{ background: l.color }} />
              <span className="text-[11px] font-medium text-white">{l.label}</span>
              <span className="tnum ml-auto text-[10px] text-slate-300">
                {i === 0
                  ? "상위 1% 이내"
                  : "상위 " + meta.levels[i - 1].max_pct + "~" + l.max_pct + "%"}
              </span>
            </div>
          ))}

          <SectionTitle className="mt-4">이 시각 전국</SectionTitle>
          <Row label="상위 1% 격자 노출인구" value={nf(s?.top1_pop ?? 0) + "명"} />
          <Row label="상위 5% 격자 노출인구" value={nf(s?.top5_pop ?? 0) + "명"} />
          <Row label="WUI ∩ 상위 5% 격자" value={nf(s?.wui_top5_cells ?? 0) + "개"} />
          <Row label="예측 구간 실제 발화" value={hourFires.length + "건"} />

          <SectionTitle className="mt-4">대응 우선지역 Top 10</SectionTitle>
          <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
            위험 순위와 SGIS 노출인구 순위의 평균. 산림 30%·인구 10명 이상 격자(WUI) 한정.
          </div>
          {top.map((p, i) => (
            <button
              key={i}
              onClick={() => {
                mapRef.current?.flyTo({ center: [p.lon, p.lat], zoom: 11, duration: 900 });
                setSel({
                  i: p.i,
                  nm: p.nm,
                  cd: "",
                  pop: p.pop,
                  hh_: 0,
                  ho: 0,
                  lowq: false,
                  top: p.top,
                  score: p.score,
                  lon: p.lon,
                  lat: p.lat,
                  sig: null,
                });
              }}
              className="mb-1 flex w-full items-center gap-2 rounded-lg border border-white/5 bg-white/[0.03] px-2 py-1.5 text-left transition hover:bg-white/10"
            >
              <span
                className={
                  "tnum flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] font-bold " +
                  (i < 3 ? "bg-red-500/80 text-white" : "bg-white/10 text-slate-300")
                }
              >
                {i + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] font-medium text-white">
                  {p.nm || "이름 없음"}
                </span>
                <span className="tnum block text-[10px] text-slate-400">
                  상위 {p.top.toFixed(2)}% · 노출 {nf(p.pop)}명
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ── 우측 패널 ─────────────────────────────────────────── */}
      {sel && (
        <div className="pointer-events-none absolute right-3 top-[4.75rem] z-10 w-[272px]">
          <div className="glass pointer-events-auto p-3">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold text-white">
                  {sel.nm || "선택 격자"}
                </div>
                <div className="tnum text-[10px] text-slate-500">
                  {sel.lat.toFixed(4)}°N {sel.lon.toFixed(4)}°E · 500m 격자
                </div>
              </div>
              <button
                onClick={() => setSel(null)}
                className="shrink-0 text-[12px] text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            {lv && (
              <div
                className="mb-3 rounded-xl px-3 py-2.5"
                style={{ background: lv.color + "26", border: "1px solid " + lv.color + "66" }}
              >
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px]">🔥</span>
                  <span className="text-[13px] font-bold" style={{ color: lv.color }}>
                    {lv.label}
                  </span>
                </div>
                <div className="tnum mt-0.5 text-[20px] font-bold leading-tight text-white">
                  전국 상위 {sel.top!.toFixed(2)}%
                </div>
                <div className="text-[10px] text-slate-400">
                  {String(hour + 1).padStart(2, "0")}시 기준 신규 발화 위험 순위
                </div>
              </div>
            )}

            <SectionTitle>SGIS 노출 · 2024 · 500m</SectionTitle>
            <Row label="인구" value={nf(sel.pop, 0) + "명"} />
            <Row label="가구" value={sel.hh_ ? nf(sel.hh_) + "가구" : "—"} />
            <Row label="주택" value={sel.ho ? nf(sel.ho) + "호" : "—"} />
            {sel.lowq && (
              <div className="mt-2 rounded-lg border border-amber-400/25 bg-amber-400/10 p-2 text-[10px] leading-relaxed text-amber-200">
                SGIS 통계 비공개 처리 구간(0·5·8)으로만 구성된 격자라 실제 값과 오차가 큽니다.
              </div>
            )}

            <SectionTitle className="mt-4">위험 상승 신호</SectionTitle>
            <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
              참고 지표가 아니라 <b className="text-slate-300">모델이 실제로 입력받은 값</b>입니다.
            </div>
            {sel.sig ? (
              meta.signals.map((g) => {
                const v = (sel.sig as any)[g.key];
                return (
                  <div key={g.key} className="flex items-baseline justify-between py-0.5">
                    <span className="text-[11px] text-slate-400">
                      {g.label}
                      <span className="ml-1 text-[9px] text-slate-600">
                        {g.dir === "up" ? "↑위험" : "↓위험"}
                      </span>
                    </span>
                    <span className="tnum text-[12px] font-medium text-white">
                      {v == null ? "—" : nf(v, 2) + g.unit}
                    </span>
                  </div>
                );
              })
            ) : (
              <div className="py-1 text-[11px] text-slate-500">
                지도에서 격자를 직접 클릭하면 표시됩니다
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 하단 타임라인 ─────────────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 p-3">
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
            <div className="tnum ml-auto flex items-center gap-2 text-[11px] text-slate-400">
              <span>
                예측 대상 {String(hour + 1).padStart(2, "0")}~{String(hour + 3).padStart(2, "0")}시
              </span>
              {hourFires.length > 0 && (
                <span className="rounded bg-white/15 px-1.5 py-0.5 text-white">
                  실제 발화 {hourFires.length}건
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

function SectionTitle({ children, className = "" }: { children: any; className?: string }) {
  return (
    <div className={"mb-1.5 text-[11px] font-semibold text-slate-200 " + className}>{children}</div>
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

/** 전국 상대 백분위 → 등급. 확률이 아니다. */
function levelOf(meta: Meta, topPct: number) {
  return meta.levels.find((l) => topPct <= l.max_pct) ?? null;
}

// ─────────────── 타입 ───────────────
type Level = { key: string; label: string; max_pct: number; color: string };
type Signal = { key: string; label: string; unit: string; dir: "up" | "down" };
type Meta = {
  date: string;
  hours: number[];
  image_corners: [number, number][];
  top_pct_shown: number;
  vector_pct: number;
  levels: Level[];
  signals: Signal[];
  summary: Record<
    string,
    {
      top1_pop: number;
      top5_pop: number;
      wui_top5_cells: number;
      top10_pop: number;
      n_fire: number;
    }
  >;
  note: string;
};
/** 시각별 셀 값 — i: cells.geojson feature id, top: 전국 위험 백분위(0=1위) */
type CellValues = Record<
  string,
  {
    i: number[];
    top: number[];
    score: (number | null)[];
    vpd: (number | null)[];
    wind: (number | null)[];
    hum4d: (number | null)[];
    prcp4d: (number | null)[];
    ndmi: (number | null)[];
  }
>;
type PriorityItem = {
  i: number;
  nm: string;
  lon: number;
  lat: number;
  top: number;
  score: number;
  pop: number;
  forest: number;
};
type Fire = { lon: number; lat: number; hh: number; loc: string; ha: number; cells: number };
type Sig = Record<string, number | null>;
type Selected = {
  i: number;
  nm: string;
  cd: string;
  pop: number;
  hh_: number;
  ho: number;
  lowq: boolean;
  top: number | null;
  score: number | null;
  lon: number;
  lat: number;
  sig: Sig | null;
};

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

type MapProps = {
  meta: Meta;
  values: CellValues;
  fires: Fire[];
  hour: number;
  onSelect: (s: Selected | null) => void;
  onReady: (m: MlMap) => void;
};

function FireMap({ meta, values, fires, hour, onSelect, onReady }: MapProps) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const loaded = useRef(false);
  const valRef = useRef(values);
  valRef.current = values;
  const hourRef = useRef(hour);
  hourRef.current = hour;

  useEffect(() => {
    let dead = false;

    (async () => {
      const maplibregl = await loadMapLibre();
      if (dead || !box.current || map.current) return;

      const m = new maplibregl.Map({
        container: box.current,
        style: STYLE,
        center: [127.9, 36.3],
        zoom: 6.4,
        minZoom: 5.5,
        maxZoom: 13,
      });
      map.current = m;
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

      // OpenFreeMap 스타일이 참조하는 스프라이트 일부(circle-11, wood-pattern)가 없어서
      // 경고가 뜨고 isStyleLoaded()가 계속 false로 남아 load 이벤트가 오지 않는 일이 있다.
      // 빈 이미지를 채워 스타일을 완결시킨다.
      m.on("styleimagemissing", (e: any) => {
        if (!m.hasImage(e.id)) {
          m.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
        }
      });

      let built = false;
      const build = () => {
        if (dead || built || !m.getStyle()) return;
        built = true;

        m.addSource("hazard", {
          type: "image",
          url: "/data/hazard_" + String(hourRef.current).padStart(2, "0") + ".png",
          coordinates: meta.image_corners,
        });
        m.addLayer({
          id: "hazard",
          type: "raster",
          source: "hazard",
          paint: { "raster-opacity": 0.85, "raster-resampling": "nearest" },
        });

        m.addSource("cells", { type: "geojson", data: "/data/cells.geojson" });
        m.addLayer({
          id: "cells-fill",
          type: "fill",
          source: "cells",
          paint: {
            "fill-color": [
              "interpolate",
              ["linear"],
              ["coalesce", ["feature-state", "top"], 99],
              0, "#ef4444",
              1, "#fb923c",
              5, "#facc15",
              10, "#a3e635",
            ],
            // 완전 투명이면 클릭이 안 잡히므로 최소값을 둔다
            "fill-opacity": ["case", ["!=", ["feature-state", "top"], null], 0.25, 0.01],
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

        m.addSource("fires", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: fires.map((f, i) => ({
              type: "Feature",
              id: i,
              geometry: { type: "Point", coordinates: [f.lon, f.lat] },
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
          if (!f) return;
          const p = f.properties as any;
          const st = m.getFeatureState({ source: "cells", id: f.id }) as any;
          const v = valRef.current[String(hourRef.current)];
          const k = v ? v.i.indexOf(f.id) : -1;
          const sig: Sig | null =
            v && k >= 0
              ? {
                  vpd: v.vpd[k],
                  wind: v.wind[k],
                  hum4d: v.hum4d[k],
                  prcp4d: v.prcp4d[k],
                  ndmi: v.ndmi[k],
                }
              : null;
          onSelect({
            i: Number(p.i),
            nm: String(p.nm ?? ""),
            cd: String(p.cd ?? ""),
            pop: Number(p.pop),
            hh_: Number(p.hh_),
            ho: Number(p.ho),
            lowq: p.lowq === true || p.lowq === "true",
            top: st?.top ?? null,
            score: st?.score ?? null,
            lon: e.lngLat.lng,
            lat: e.lngLat.lat,
            sig,
          });
        });
        m.on("mouseenter", "cells-fill", () => (m.getCanvas().style.cursor = "pointer"));
        m.on("mouseleave", "cells-fill", () => (m.getCanvas().style.cursor = ""));

        const pop = new maplibregl.Popup({ closeButton: false, offset: 12 });
        m.on("mouseenter", "fires", (e: any) => {
          const p = e.features?.[0]?.properties as any;
          if (!p) return;
          m.getCanvas().style.cursor = "pointer";
          pop
            .setLngLat(e.lngLat)
            .setHTML(
              '<div style="font:12px Pretendard,sans-serif;color:#0f172a"><b>실제 발화</b><br/>' +
                p.loc +
                "<br/>" +
                String(p.hh).padStart(2, "0") +
                "시 · " +
                p.ha +
                "ha</div>"
            )
            .addTo(m);
        });
        m.on("mouseleave", "fires", () => {
          m.getCanvas().style.cursor = "";
          pop.remove();
        });

        loaded.current = true;
        onReady(m);
        applyHour(m, valRef.current, hourRef.current);
      };

      // load 가 오면 그때, 안 오면 스타일 파싱 직후에라도 올린다.
      m.on("load", build);
      m.on("styledata", () => {
        if (m.getStyle()?.layers?.length) build();
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

  useEffect(() => {
    const m = map.current;
    if (!m || !loaded.current) return;
    const src = m.getSource("hazard") as any;
    src?.updateImage?.({ url: "/data/hazard_" + String(hour).padStart(2, "0") + ".png" });
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
