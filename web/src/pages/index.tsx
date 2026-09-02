import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type MlMap = any;

const nf = (n: number, d = 0) =>
  n.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
const pad = (n: number) => String(n).padStart(2, "0");

export default function Home() {
  const [root, setRoot] = useState<Root | null>(null);
  const [cells, setCells] = useState<Cells | null>(null);
  const [ymd, setYmd] = useState<string | null>(null);
  const [day, setDay] = useState<DayData | null>(null);
  const [hour, setHour] = useState(12);
  const [sel, setSel] = useState<Selected | null>(null);
  const [playing, setPlaying] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [timeRisk, setTimeRisk] = useState<TimeRisk | null>(null);
  const [tl, setTl] = useState<Timeline | null>(null);
  const [ti, setTi] = useState(0);
  // false = 전 기간 모드(741일, 하루 한 장). true = 사례일 시간대별 상세.
  const [detail, setDetail] = useState(false);
  const mapRef = useRef<MlMap | null>(null);
  const stripRef = useRef<HTMLCanvasElement | null>(null);

  // 최초 로드 — 사례일 목록과 전 일자 공용 격자
  useEffect(() => {
    Promise.all([
      fetch("/data/days.json").then((r) => r.json()),
      fetch("/data/cells.json").then((r) => r.json()),
    ]).then(([r, c]: [Root, Cells]) => {
      setRoot(r);
      setCells(c);
      const pref = r.days.find((d) => d.n_fire > 0) ?? r.days[0];
      setYmd(pref.ymd);
    });
  }, []);

  useEffect(() => {
    fetch("/data/timeline.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((t: Timeline | null) => {
        if (!t) return;
        setTl(t);
        let bi = 0;
        t.days.forEach((x, i) => {
          if (x.ha > t.days[bi].ha) bi = i;
        });
        setTi(bi);
      })
      .catch(() => setTl(null));
  }, []);

  // 시간축 위험등급. 54번 미실행이면 파일이 없으므로 해당 블록만 숨긴다.
  useEffect(() => {
    fetch("/data/time_risk.json")
      .then((r) => (r.ok ? r.json() : null))
      .then(setTimeRisk)
      .catch(() => setTimeRisk(null));
  }, []);

  // 연도 눈금 — 741일이 2~6월만 있어 균등 분포가 아니다. 연도 첫 등장 위치를 쓴다.
  const yearTicks = useMemo(() => {
    const seen = new Map<string, number>();
    (tl?.days ?? []).forEach((x, i) => {
      const y = x.d.slice(0, 4);
      if (!seen.has(y)) seen.set(y, i);
    });
    return [...seen].map(([y, i]) => ({ y, i }));
  }, [tl]);

  useEffect(() => {
    const cv = stripRef.current;
    if (!cv || !tl) return;
    cv.width = tl.days.length;
    cv.height = 1;
    const g = cv.getContext("2d");
    if (!g) return;
    tl.days.forEach((x, i) => {
      g.fillStyle = x.l ? timeLevelColor(x.l) : "#334155";
      g.fillRect(i, 0, 1, 1);
    });
  }, [tl]);

  const td = tl && tl.days.length ? tl.days[Math.min(ti, tl.days.length - 1)] : null;
  const tYmd = td ? td.d.replace(/-/g, "") : null;

  // 상세 모드로 들어갈 때만 그 날의 시간대별 자산을 불러온다.
  useEffect(() => {
    if (!detail) return;
    if (td && td.c) setYmd(tYmd);
  }, [detail, td, tYmd]);

  // 선택한 날의 자산
  useEffect(() => {
    if (!ymd || !detail) return;
    setDay(null);
    setSel(null);
    Promise.all([
      fetch(`/data/d/${ymd}/meta.json`).then((r) => r.json()),
      fetch(`/data/d/${ymd}/values.json`).then((r) => r.json()),
      fetch(`/data/d/${ymd}/priority.json`).then((r) => r.json()),
      fetch(`/data/d/${ymd}/fires.json`).then((r) => r.json()),
    ]).then(([meta, values, priority, fires]) => {
      setDay({ meta, values, priority, fires });
      setHour(meta.hours.includes(12) ? 12 : meta.hours[0]);
    });
  }, [ymd]);

  useEffect(() => {
    if (!playing || !day) return;
    const hs = day.meta.hours;
    const id = setInterval(() => setHour((h) => hs[(hs.indexOf(h) + 1) % hs.length]), 1100);
    return () => clearInterval(id);
  }, [playing, day]);

  const info = root && ymd ? root.days.find((d) => d.ymd === ymd) ?? null : null;
  const s = day?.meta.summary[String(hour)];
  const topList = day?.priority[String(hour)] ?? [];
  const hourFires = useMemo(
    () => (day?.fires ?? []).filter((f) => f.hh >= hour + 1 && f.hh <= hour + 3),
    [day, hour]
  );

  // 이 날 이 시각이 5년(또는 해당 연도) 분포에서 어디쯤인가
  // 시간축 등급은 "그날이 5년 중 어느 정도인가"라 하루 단위 속성이다. 상세 모드에서
  // 시각을 움직여도 바뀌지 않는다. 또 time_risk 는 스캔한 시각(8·10·11·14)만 갖고
  // 있어서 06~18시를 그대로 넘기면 대부분 빈 값이 된다. 대표 시각 하나로 고정한다.
  const trDate = detail ? (info?.date ?? null) : (td?.d ?? null);
  const tr =
    timeRisk && trDate ? timeRisk.days[trDate]?.[String(tl?.hour ?? 10)] ?? null : null;

  const pickCell = useCallback(
    (k: number, lon: number, lat: number) => {
      if (!cells || !day) return;
      const v = day.values.hours[String(hour)];
      const j = v ? v.i.indexOf(k) : -1;
      const sc = day.values.scale;
      const dj = day.values.daily.i.indexOf(k);
      const dv = (arr: (number | null)[], scale: number) =>
        dj >= 0 && arr[dj] != null ? arr[dj]! / scale : null;
      setSel({
        k,
        nm: cells.nms[cells.nmi[k]] || "",
        pop: cells.pop[k],
        hh_: cells.hh[k],
        ho: cells.ho[k],
        lowq: cells.lowq[k] === 1,
        top: j >= 0 ? v.top[j] / sc.top : null,
        lon,
        lat,
        sig:
          j >= 0
            ? {
                vpd: v.vpd[j] == null ? null : v.vpd[j]! / sc.vpd,
                wind: v.wind[j] == null ? null : v.wind[j]! / sc.wind,
                hum4d: dv(day.values.daily.hum4d, sc.hum4d),
                prcp4d: dv(day.values.daily.prcp4d, sc.prcp4d),
                ndmi: dv(day.values.daily.ndmi, sc.ndmi),
              }
            : null,
      });
    },
    [cells, day, hour]
  );

  if (!root || !cells) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">
        데이터 불러오는 중…
      </div>
    );
  }

  const hours = day?.meta.hours ?? [];
  const idx = Math.max(0, hours.indexOf(hour));
  const lv = sel && sel.top != null ? levelOf(root, sel.top) : null;
  const byYear = groupByYear(root.days);

  return (
    <main className="relative h-full w-full overflow-hidden bg-ink">
      <FireMap
        root={root}
        cells={cells}
        values={day?.values ?? null}
        fires={
          detail
            ? (day?.fires ?? [])
            : (td?.ft ?? []).map((f) => ({
                lon: f[3],
                lat: f[4],
                hh: f[1],
                loc: f[0],
                ha: f[2],
                cells: 0,
              }))
        }
        ymd={detail ? ymd : null}
        hour={hour}
        dailyPng={detail || !tYmd ? null : `/data/daily/${tYmd}_${pad(tl?.hour ?? 10)}.png`}
        onPick={pickCell}
        onReady={(m: MlMap) => (mapRef.current = m)}
      />

      {/* ── 헤더 ─────────────────────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex items-start gap-3 p-3">
        <div className="glass pointer-events-auto flex items-center gap-2 px-3 py-2">
          <span className="text-lg">🔥</span>
          <div className="leading-tight">
            <div className="text-[14px] font-semibold text-white">산불先지도</div>
            <div className="text-[10px] text-slate-400">AI 발화예측 × SGIS 공간통계</div>
          </div>
        </div>

        <div className="pointer-events-auto relative ml-auto">
          <button
            onClick={() => setPickerOpen((o) => !o)}
            className="glass flex items-center gap-2 px-3 py-2 text-right transition hover:bg-white/10"
          >
            <div>
              <div className="text-[10px] text-slate-400">
                {detail
                  ? `시간대별 상세 · 사례일 ${root.days.length}일`
                  : tl
                    ? `전 기간 ${tl.days.length}일 · ${tl.note}`
                    : "신규발화 위험"}
              </div>
              <div className="tnum text-[14px] font-semibold text-white">
                {(detail ? info?.date : td?.d) ?? "—"}{" "}
                <span className="text-accent">
                  {pad(detail ? hour : (tl?.hour ?? 10))}:00
                </span>{" "}
                KST
              </div>
            </div>
            <span className="text-[10px] text-slate-400">{pickerOpen ? "▲" : "▼"}</span>
          </button>

          {pickerOpen && (
            <div className="glass scroll-thin absolute right-0 top-full mt-2 max-h-[70vh] w-[330px] overflow-y-auto p-3">
              <div className="mb-2 text-[10px] leading-relaxed text-slate-400">
                선정 규칙 —{" "}
                <b className="text-slate-300">
                  연도별 피해 상위 {root.rule.top_damage_per_year}일
                </b>{" "}
                + <b className="text-slate-300">무작위 {root.rule.random_per_year}일</b> (seed{" "}
                {root.rule.seed}). 모델 예측 결과는 선정에 쓰지 않았고, 발화가 없던 날도 그대로
                포함합니다.
              </div>
              {byYear.map(([y, list]) => (
                <div key={y} className="mb-2">
                  <div className="tnum mb-1 text-[11px] font-semibold text-slate-300">{y}년</div>
                  {list.map((d) => (
                    <button
                      key={d.ymd}
                      onClick={() => {
                        setYmd(d.ymd);
                        setPickerOpen(false);
                      }}
                      className={
                        "mb-1 flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition " +
                        (d.ymd === ymd
                          ? "border-accent/60 bg-accent/15"
                          : "border-white/5 bg-white/[0.03] hover:bg-white/10")
                      }
                    >
                      <span
                        className={
                          "shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium " +
                          (d.reason === "top_damage"
                            ? "bg-red-500/70 text-white"
                            : "bg-white/10 text-slate-300")
                        }
                      >
                        {d.reason === "top_damage" ? "피해상위" : "무작위"}
                      </span>
                      <span className="tnum flex-1 text-[11px] text-white">{d.date}</span>
                      <span className="tnum text-[10px] text-slate-400">
                        {d.n_fire === 0 ? "발화 없음" : `${d.n_fire}건 · ${nf(d.ha, 1)}ha`}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
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
          {root.levels.map((l, i) => (
            <div
              key={l.key}
              className="mb-1 flex items-center gap-2 rounded-lg px-2 py-1.5"
              style={{ background: l.color + "22", border: "1px solid " + l.color + "55" }}
            >
              <span className="h-3 w-3 shrink-0 rounded" style={{ background: l.color }} />
              <span className="text-[11px] font-medium text-white">{l.label}</span>
              <span className="tnum ml-auto text-[10px] text-slate-300">
                {i === 0 ? "상위 1% 이내" : `상위 ${root.levels[i - 1].max_pct}~${l.max_pct}%`}
              </span>
            </div>
          ))}

          {tr && timeRisk && (
            <>
              <SectionTitle className="mt-4">이 날의 전국 위험 수준</SectionTitle>
              <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
                위 등급은 <b className="text-slate-300">그날 안에서의 공간 순위</b>라 조용한 날에도
                상위 1%는 늘 나옵니다. 이 값은 <b className="text-slate-300">{timeRisk.note}</b>{" "}
                오늘이 어느 정도인지를 따로 잰 것입니다.
              </div>
              <div
                className="mb-1 flex items-center gap-2 rounded-lg px-2 py-2"
                style={{
                  background: timeLevelColor(tr[1]) + "22",
                  border: "1px solid " + timeLevelColor(tr[1]) + "55",
                }}
              >
                <span
                  className="h-3 w-3 shrink-0 rounded"
                  style={{ background: timeLevelColor(tr[1]) }}
                />
                <span className="text-[12px] font-semibold text-white">{tr[1]}</span>
                <span className="tnum ml-auto text-[10px] text-slate-300">
                  상위 {nf(100 - tr[0], 1)}%
                </span>
              </div>
              <div className="mb-1 h-1.5 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${tr[0]}%`, background: timeLevelColor(tr[1]) }}
                />
              </div>
            </>
          )}

          <SectionTitle className="mt-4">이 시각 전국</SectionTitle>
          {detail ? (
            day ? (
              <>
                <Row label="상위 1% 격자 노출인구" value={nf(s?.top1_pop ?? 0) + "명"} />
                <Row label="상위 5% 격자 노출인구" value={nf(s?.top5_pop ?? 0) + "명"} />
                <Row label="WUI ∩ 상위 5% 격자" value={nf(s?.wui_top5_cells ?? 0) + "개"} />
                <Row label="예측 구간 실제 발화" value={hourFires.length + "건"} />
              </>
            ) : (
              <div className="py-1 text-[11px] text-slate-500">불러오는 중…</div>
            )
          ) : td ? (
            <>
              <Row label="상위 1% 격자 노출인구" value={nf(td.e1) + "명"} />
              <Row label="상위 5% 격자 노출인구" value={nf(td.e5) + "명"} />
              <Row label="WUI ∩ 상위 5% 격자" value={nf(td.w) + "개"} />
              <Row label="이 날 실제 발화" value={td.n === 0 ? "없음" : `${td.n}건 · ${nf(td.ha, 1)}ha`} />
            </>
          ) : (
            <div className="py-1 text-[11px] text-slate-500">불러오는 중…</div>
          )}

          {!detail && td && td.ft.length > 0 && (
            <>
              <SectionTitle className="mt-4">이 날 실제 발화</SectionTitle>
              <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
                예측과 별개로 그날 실제 기록된 산불입니다. 지도의 붉은 영역과 겹치는지 보세요.
              </div>
              {td.ft.map((f, i) => (
                <div
                  key={i}
                  className="mb-1 flex items-center gap-2 rounded-lg border border-white/5 bg-white/[0.03] px-2 py-1.5"
                >
                  <span className="tnum shrink-0 text-[10px] text-slate-400">{pad(f[1])}시</span>
                  <span className="flex-1 truncate text-[11px] text-white">{f[0] || "—"}</span>
                  <span className="tnum text-[10px] text-slate-300">{nf(f[2], 1)}ha</span>
                </div>
              ))}
            </>
          )}

          <SectionTitle className="mt-4">대응 우선지역 Top 10</SectionTitle>
          <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
            위험 순위와 SGIS 노출인구 순위의 평균. 산림 30%·인구 10명 이상 격자(WUI) 한정.
          </div>
          {!detail && (
            <div className="mb-1 rounded-lg border border-white/5 bg-white/[0.03] px-2 py-2 text-[10px] leading-relaxed text-slate-400">
              전 기간 {tl?.days.length ?? 0}일은 격자 단위 값을 저장하지 않습니다(수십 GB).
              우선지역 목록은 <b className="text-slate-300">시간대별 상세 {root.days.length}일</b>에서
              볼 수 있습니다.
            </div>
          )}
          {detail && topList.length === 0 && (
            <div className="py-1 text-[11px] text-slate-500">
              {day ? "이 시각 해당 격자 없음" : "불러오는 중…"}
            </div>
          )}
          {topList.map((p, i) => (
            <button
              key={i}
              onClick={() => {
                mapRef.current?.flyTo({ center: [p.lon, p.lat], zoom: 11, duration: 900 });
                if (p.i >= 0) pickCell(p.i, p.lon, p.lat);
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

            {lv ? (
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
                  {pad(hour + 1)}시 기준 신규 발화 위험 순위
                </div>
              </div>
            ) : (
              <div className="mb-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-[11px] text-slate-400">
                이 시각에는 상위 {root.vector_pct}% 밖이라 순위를 표시하지 않습니다.
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
              root.signals.map((g) => {
                const v = sel.sig![g.key];
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
              <div className="py-1 text-[11px] text-slate-500">이 시각 값 없음</div>
            )}
          </div>
        </div>
      )}

      {/* ── 하단 바 — 전 기간 스크러버 / 사례일 시간 슬라이더 ── */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 p-3">
        <div className="glass pointer-events-auto mx-auto max-w-4xl px-4 py-3">
          {detail ? (
            <>
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
                <button
                  onClick={() => {
                    setPlaying(false);
                    setDetail(false);
                  }}
                  className="pill ml-auto bg-white/10 px-2.5 py-1 text-[10px] text-slate-200 hover:bg-white/20"
                >
                  ← 전 기간으로
                </button>
              </div>
              <input
                type="range"
                className="tick w-full"
                min={0}
                max={Math.max(0, hours.length - 1)}
                step={1}
                value={idx}
                disabled={!day}
                onChange={(e) => setHour(hours[Number(e.target.value)])}
              />
              <div className="tnum mt-0.5 flex justify-between text-[10px] text-slate-500">
                {hours.map((h) => (
                  <span key={h} className={h === hour ? "font-bold text-accent" : ""}>
                    {pad(h)}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="mb-1.5 flex items-center gap-3">
                <div className="text-[11px] text-slate-300">
                  {tl ? (
                    <>
                      <b className="text-white">{tl.days.length}일</b> 전 기간을 매일 산출했습니다.
                      막대를 끌면 그날의 전국 위험지도가 바뀝니다
                    </>
                  ) : (
                    "전 기간 불러오는 중…"
                  )}
                </div>
                <div className="tnum ml-auto flex items-center gap-2 text-[11px]">
                  {td?.l && (
                    <span
                      className="rounded px-1.5 py-0.5 text-white"
                      style={{ background: timeLevelColor(td.l) + "cc" }}
                    >
                      {td.l}
                    </span>
                  )}
                  {td && td.n > 0 && (
                    <span className="rounded bg-white/15 px-1.5 py-0.5 text-white">
                      실제 발화 {td.n}건
                    </span>
                  )}
                  {td?.c === 1 && (
                    <button
                      onClick={() => setDetail(true)}
                      className="pill bg-accent/70 px-2.5 py-1 text-[10px] text-white hover:bg-accent"
                    >
                      시간대별 상세 →
                    </button>
                  )}
                </div>
              </div>

              {/* 741일 위험등급 띠 — div 737개는 각 1px 미만이라 0으로 찌그러진다.
                  하루 1픽셀로 캔버스에 그리고 CSS 로 늘린다. */}
              <div
                className="relative mb-1 h-5 w-full cursor-pointer overflow-hidden rounded"
                onClick={(e) => {
                  if (!tl) return;
                  const r = e.currentTarget.getBoundingClientRect();
                  const f = (e.clientX - r.left) / r.width;
                  setTi(Math.max(0, Math.min(tl.days.length - 1, Math.round(f * (tl.days.length - 1)))));
                }}
              >
                <canvas
                  ref={stripRef}
                  className="h-full w-full"
                  style={{ imageRendering: "pixelated" }}
                />
                {tl && (
                  <div
                    className="pointer-events-none absolute top-0 h-full w-[2px] bg-white shadow"
                    style={{ left: `calc(${(ti / Math.max(1, tl.days.length - 1)) * 100}% - 1px)` }}
                  />
                )}
              </div>
              <input
                type="range"
                className="tick w-full"
                min={0}
                max={Math.max(0, (tl?.days.length ?? 1) - 1)}
                step={1}
                value={ti}
                disabled={!tl}
                onChange={(e) => setTi(Number(e.target.value))}
              />
              <div className="tnum mt-0.5 flex justify-between text-[10px] text-slate-500">
                {yearTicks.map((t) => (
                  <span key={t.y} className={t.y === td?.d.slice(0, 4) ? "font-bold text-accent" : ""}>
                    {t.y}
                  </span>
                ))}
              </div>
            </>
          )}
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

/** 시간축 등급 색. 지도 등급과 같은 색계열을 쓰되 별개 축임을 알 수 있게 한다. */
const timeLevelColor = (name: string) =>
  ({ "매우 높음": "#ef4444", 높음: "#f97316", 주의: "#eab308", 보통: "#84cc16" } as Record<
    string,
    string
  >)[name] ?? "#64748b";

/** 전국 상대 백분위 → 등급. 확률이 아니다. */
function levelOf(root: Root, topPct: number) {
  return root.levels.find((l) => topPct <= l.max_pct) ?? null;
}

function groupByYear(days: DayInfo[]): [number, DayInfo[]][] {
  const m = new Map<number, DayInfo[]>();
  days.forEach((d) => m.set(d.year, [...(m.get(d.year) ?? []), d]));
  return [...m.entries()].sort((a, b) => a[0] - b[0]);
}

// ─────────────── 타입 ───────────────
type Level = { key: string; label: string; max_pct: number; color: string };
/** 55번 산출. 741일 전 기간을 하나의 날짜축에 세운다. 하루 약 160바이트. */
type TlDay = {
  d: string;            // YYYY-MM-DD
  p: number | null;     // 시간축 백분위 (5년 중 오늘의 위치)
  l: string | null;     // 등급
  e1: number;           // 상위 1% 격자 노출인구
  e5: number;
  w: number;            // WUI ∩ 상위 5% 격자 수
  n: number;            // 그날 실제 발화 건수
  ha: number;
  ft: [string, number, number, number, number][]; // [지명, 시각, 피해ha, lon, lat]
  c: 0 | 1;             // 시간대별 상세 자산 보유 여부
};
type Timeline = { hour: number; note: string; basis: string; levels: string[]; days: TlDay[] };
/** 54번 산출. 지도의 공간 백분위와 달리 "5년(또는 해당 연도) 중 오늘이 몇 등인가"를 잰다. */
type TimeRisk = {
  basis: "통합" | "연도별";
  levels: string[];
  note: string;
  days: Record<string, Record<string, [number, string]>>;
};
type Signal = { key: string; label: string; unit: string; dir: "up" | "down" };
type DayInfo = {
  date: string;
  ymd: string;
  year: number;
  reason: "top_damage" | "random";
  n_fire: number;
  ha: number;
  cells: number;
  hours: number[];
};
type Root = {
  rule: { top_damage_per_year: number; random_per_year: number; seed: number; note: string };
  image_corners: [number, number][];
  levels: Level[];
  signals: Signal[];
  vector_pct: number;
  top_pct_shown: number;
  days: DayInfo[];
  note: string;
};
/** 전 일자 공용 격자 — b는 [lon0,lat0,lon1,lat1] × 1e5 정수를 셀마다 4개씩 나열 */
type Cells = {
  n: number;
  b: number[];
  nms: string[];
  nmi: number[];
  pop: number[];
  hh: number[];
  ho: number[];
  lowq: number[];
};
type Values = {
  scale: Record<string, number>;
  daily: {
    i: number[];
    hum4d: (number | null)[];
    prcp4d: (number | null)[];
    ndmi: (number | null)[];
  };
  hours: Record<
    string,
    { i: number[]; top: number[]; vpd: (number | null)[]; wind: (number | null)[] }
  >;
};
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
type DayMeta = {
  date: string;
  hours: number[];
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
};
type DayData = {
  meta: DayMeta;
  values: Values;
  priority: Record<string, PriorityItem[]>;
  fires: Fire[];
};
type Sig = Record<string, number | null>;
type Selected = {
  k: number;
  nm: string;
  pop: number;
  hh_: number;
  ho: number;
  lowq: boolean;
  top: number | null;
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

/** 압축된 셀 배열 → GeoJSON (클라이언트에서 1회 생성) */
function cellsToGeoJSON(c: Cells) {
  const features = new Array(c.n);
  for (let i = 0; i < c.n; i++) {
    const o = i * 4;
    const x0 = c.b[o] / 1e5;
    const y0 = c.b[o + 1] / 1e5;
    const x1 = c.b[o + 2] / 1e5;
    const y1 = c.b[o + 3] / 1e5;
    features[i] = {
      type: "Feature",
      id: i,
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [x0, y1],
            [x1, y1],
            [x1, y0],
            [x0, y0],
            [x0, y1],
          ],
        ],
      },
      properties: { i },
    };
  }
  return { type: "FeatureCollection", features };
}

type MapProps = {
  root: Root;
  cells: Cells;
  values: Values | null;
  fires: Fire[];
  ymd: string | null;
  hour: number;
  /** 전 기간 모드에서 띄울 일별 PNG. 지정되면 사례일 시간별 PNG 대신 이걸 쓴다. */
  dailyPng: string | null;
  onPick: (k: number, lon: number, lat: number) => void;
  onReady: (m: MlMap) => void;
};

function FireMap({ root, cells, values, fires, ymd, hour, dailyPng, onPick, onReady }: MapProps) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  // ref 가 아니라 state 다. 지도 구축이 데이터 로드보다 늦게 끝나는 경우가 있는데,
  // ref 로는 렌더가 트리거되지 않아 아래 갱신 effect 들이 다시 돌지 않는다.
  const [ready, setReady] = useState(false);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

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
      // isStyleLoaded()가 계속 false로 남고 load 이벤트가 오지 않는 일이 있다.
      m.on("styleimagemissing", (e: any) => {
        if (!m.hasImage(e.id)) m.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
      });

      let built = false;
      const build = () => {
        if (dead || built || !m.getStyle()) return;
        built = true;

        m.addSource("hazard", {
          type: "image",
          url: TRANSPARENT_PNG,
          coordinates: root.image_corners,
        });
        m.addLayer({
          id: "hazard",
          type: "raster",
          source: "hazard",
          paint: { "raster-opacity": 0.85, "raster-resampling": "nearest" },
        });

        m.addSource("cells", { type: "geojson", data: cellsToGeoJSON(cells) });
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
              3, "#facc15",
              5, "#a3e635",
            ],
            // 완전 투명이면 클릭이 안 잡히므로 최소값을 둔다
            "fill-opacity": ["case", ["!=", ["feature-state", "top"], null], 0.28, 0.01],
          },
        });

        m.addSource("fires", { type: "geojson", data: emptyFC() });
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
          if (f) pickRef.current(Number(f.id), e.lngLat.lng, e.lngLat.lat);
        });
        m.on("mouseenter", "cells-fill", () => (m.getCanvas().style.cursor = "pointer"));
        m.on("mouseleave", "cells-fill", () => (m.getCanvas().style.cursor = ""));

        const pop = new maplibregl.Popup({ closeButton: false, offset: 12 });
        m.on("mouseenter", "fires", (e: any) => {
          const p = e.features?.[0]?.properties;
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

        setReady(true);
        onReady(m);
      };

      m.on("load", build);
      m.on("styledata", () => {
        if (m.getStyle()?.layers?.length) build();
      });
    })();

    return () => {
      dead = true;
      map.current?.remove();
      map.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 날짜·시각 변경 → 배경 PNG와 셀 색상 갱신
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const src = m.getSource("hazard") as any;

    // 전 기간 모드에는 셀 단위 값이 없다(741일치 격자를 다 저장하면 수십 GB).
    // 배경 PNG 만 갈아끼우고 벡터 셀 레이어는 숨긴다.
    if (dailyPng) {
      src?.updateImage?.({ url: dailyPng });
      m.removeFeatureState({ source: "cells" });
      if (m.getLayer("cells-fill")) m.setLayoutProperty("cells-fill", "visibility", "none");
      return;
    }
    if (m.getLayer("cells-fill")) m.setLayoutProperty("cells-fill", "visibility", "visible");
    if (!ymd || !values) return;
    src?.updateImage?.({ url: `/data/d/${ymd}/hazard_${pad(hour)}.png` });

    m.removeFeatureState({ source: "cells" });
    const v = values.hours[String(hour)];
    if (v) {
      const sc = values.scale.top;
      for (let k = 0; k < v.i.length; k++) {
        m.setFeatureState({ source: "cells", id: v.i[k] }, { top: v.top[k] / sc });
      }
    }
  }, [ready, ymd, hour, values, dailyPng]);

  // 실제 발화점 갱신
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const src = m.getSource("fires") as any;
    src?.setData?.({
      type: "FeatureCollection",
      features: fires.map((f, i) => ({
        type: "Feature",
        id: i,
        geometry: { type: "Point", coordinates: [f.lon, f.lat] },
        properties: { ...f },
      })),
    });
  }, [ready, fires]);

  return <div ref={box} className="absolute inset-0" />;
}

function emptyFC() {
  return { type: "FeatureCollection", features: [] as any[] };
}

/** 1×1 투명 PNG — 날짜가 정해지기 전 image source의 자리표시자 */
const TRANSPARENT_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
