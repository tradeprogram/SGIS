import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChatPanel, { type ChatContext } from "../components/ChatPanel";
import RegionPicker, { type Picked } from "../components/RegionPicker";
import WhyPanel from "../components/WhyPanel";
import { resolveRegion, queryRegion, type AdmIndexLite } from "../lib/spatialQuery";

type MlMap = any;

// 행정경계는 6.6MB 다. 컴포넌트가 다시 마운트될 때마다 받으면 그대로 낭비라
// 모듈 수준에서 한 번만 받아 재사용한다.
let admPromise: Promise<any> | null = null;
function loadAdm() {
  if (!admPromise) {
    admPromise = fetch("/data/adm_dong.geojson")
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return admPromise;
}

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
  // 왼쪽에서 고른 행정동. 지도 경계 강조와 화면 이동에 쓴다.
  const [picked, setPicked] = useState<Picked | null>(null);
  // 우선지역에서 근거(occlusion 기여도)를 펼쳐 놓은 항목
  const [whyOpen, setWhyOpen] = useState<number | null>(null);
  // 공간질의용 행정동 인덱스. RegionPicker 와 같은 파일을 쓰지만 여기서는
  // 이름→코드만 필요해서 가볍게 따로 들고 있는다.
  const [admIdx, setAdmIdx] = useState<AdmIndexLite | null>(null);
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

  useEffect(() => {
    fetch("/data/adm_index.json")
      .then((r) => (r.ok ? r.json() : null))
      .then(setAdmIdx)
      .catch(() => setAdmIdx(null));
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
  const chatCtx: ChatContext = useMemo(
    () => ({
      mode: detail ? "detail" : "timeline",
      hour: detail ? hour : (tl?.hour ?? 10),
      day: detail
        ? info
          ? {
              date: info.date, level: null, topPct: null,
              e1: Math.round(s?.top1_pop_day ?? 0), e5: Math.round(s?.top5_pop_day ?? 0),
              wui: s?.wui_top5_cells ?? 0, n: info.n_fire, ha: info.ha,
              fires: (day?.fires ?? []).map((f) => ({ loc: f.loc, hh: f.hh, ha: f.ha })),
            }
          : null
        : td
          ? {
              // 화면 표기와 같은 방향으로 넘긴다. 원값(td.p)은 아래에서부터의
              // 백분위라 그대로 주면 모델이 정반대로 읽는다.
              date: td.d, level: td.l, topPct: td.p == null ? null : Math.round((100 - td.p) * 10) / 10,
              e1: td.e1, e5: td.e5, wui: td.w, n: td.n, ha: td.ha,
              fires: td.ft.map((f) => ({ loc: f[0], hh: f[1], ha: f[2] })),
            }
          : null,
      priority: detail
        ? topList.slice(0, 10).map((t, i) => ({
            rank: i + 1, name: t.nm, topPct: t.top, pop: t.pop,
            popDay: t.popd, popOld: t.old, avgAge: t.age,
          }))
        : undefined,
      cell: sel
        ? { name: sel.nm, topPct: sel.top, pop: sel.pop, signals: sel.sig ?? null }
        : null,
    }),
    [detail, hour, tl, info, s, day, td, topList, sel]
  );

  // 질문에서 지역을 찾아 현재 시각 위험도를 집계한다.
  // 전 기간 모드에는 격자 단위 값이 없어(수십 GB) null 을 돌려준다.
  const spatialQuery = useCallback(
    (q: string) => {
      if (!admIdx || !cells || !detail || !day) return null;
      const hit = resolveRegion(q, admIdx as AdmIndexLite);
      if (!hit) return null;
      const hv = day.values.hours[String(hour)] ?? null;
      return queryRegion(hit, cells as any, hv, day.values.scale.top);
    },
    [admIdx, cells, detail, day, hour]
  );

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
        picked={picked}
        onPick={pickCell}
        onReady={(m: MlMap) => (mapRef.current = m)}
      />

      <ChatPanel ctx={chatCtx} spatial={spatialQuery} />

      {/* ── 헤더 ─────────────────────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex items-start gap-3 p-3">
        <div className="glass hud pointer-events-auto flex items-center gap-2.5 px-3 py-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/logo.png"
            alt="산불 발화예측·우선대응 통합지도"
            width={34}
            height={34}
            className="h-[34px] w-[34px] shrink-0 drop-shadow-[0_0_12px_rgba(239,68,68,0.45)]"
          />
          <div className="leading-tight">
            <div className="text-[13px] font-semibold leading-tight text-white">
              산불 발화예측·우선대응 통합지도
            </div>
            <div className="text-[10px] leading-tight text-sky-300/60">SGIS 통계지리 기반 500m 격자 의사결정 시스템</div>
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
              <div className="tnum glow text-[14px] font-semibold text-white">
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
        <div className="glass hud scroll-thin pointer-events-auto h-full overflow-y-auto p-3">
          <SectionTitle>지역 찾기</SectionTitle>
          <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
            읍·면·동을 고르면 그 경계를 <b className="text-sky-300">파랑</b>으로 짚고 화면을
            옮깁니다. 전국 <b className="text-slate-300">3,559개</b> 행정동, SGIS 경계 기준입니다.
          </div>
          <RegionPicker picked={picked} onPick={setPicked} />

          <SectionTitle className="mt-4">위험 등급</SectionTitle>
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
                <Row label="상위 1% 주간 노출인구" value={nf(s?.top1_pop_day ?? 0) + "명"} />
                <Row label="상위 5% 주간 노출인구" value={nf(s?.top5_pop_day ?? 0) + "명"} />
                <Row label="└ 65세 이상" value={nf(s?.top5_pop_old ?? 0) + "명"} />
                <Row label="└ 30년 이상 노후주택" value={nf(s?.top5_old_house ?? 0) + "호"} />
                <Row label="상위 5% 상주인구(야간)" value={nf(s?.top5_pop ?? 0) + "명"} />
                <Row label="WUI ∩ 상위 5% 격자" value={nf(s?.wui_top5_cells ?? 0) + "개"} />
                <Row label="예측 구간 실제 발화" value={hourFires.length + "건"} />
              </>
            ) : (
              <div className="py-1 text-[11px] text-slate-500">불러오는 중…</div>
            )
          ) : td ? (
            <>
              <Row label="상위 1% 주간 노출인구" value={nf(td.e1) + "명"} />
              <Row label="상위 5% 주간 노출인구" value={nf(td.e5) + "명"} />
              <Row label="└ 65세 이상" value={nf(td.o5 ?? 0) + "명"} />
              <Row label="└ 30년 이상 노후주택" value={nf(td.h5 ?? 0) + "호"} />
              <Row label="상위 5% 상주인구(야간)" value={nf(td.r5 ?? 0) + "명"} />
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
            위험 순위와 SGIS <b className="text-slate-300">주간 노출인구</b> 순위의 평균.
            산림 30%·인구 10명 이상 격자(WUI) 한정.
          </div>
          <div className="mb-1.5 rounded-lg border border-amber-400/20 bg-amber-400/5 px-2 py-1.5 text-[10px] leading-relaxed text-slate-400">
            산불은 오후에 나는데 상주인구는 야간 기준입니다. 그래서 SGIS 종사자수로
            <b className="text-slate-300"> 주간인구를 추정</b>해 보정했습니다. 다만 농작업은
            사업체 등록에 안 잡혀 <b className="text-slate-300">농촌은 과소추정</b>됩니다.
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
            <div key={i} className="mb-1">
            <button
              onClick={() => {
                mapRef.current?.flyTo({ center: [p.lon, p.lat], zoom: 11, duration: 900 });
                if (p.i >= 0) pickCell(p.i, p.lon, p.lat);
                setWhyOpen(whyOpen === i ? null : i);
              }}
              className="flex w-full items-center gap-2 rounded-lg border border-white/5 bg-white/[0.03] px-2 py-1.5 text-left transition hover:bg-white/10"
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
                  상위 {p.top.toFixed(2)}% · 주간 {nf(p.popd)}명
                  {p.old > 0 && <> · 65+ {nf(p.old)}명</>}
                </span>
              </span>
              {p.ot && (
                <span className="shrink-0 text-[10px] text-slate-500">
                  {whyOpen === i ? "닫기" : "왜?"}
                </span>
              )}
            </button>
            {whyOpen === i && p.ot && p.os && <WhyPanel ot={p.ot} os={p.os} />}
            </div>
          ))}
        </div>
      </div>

      {/* ── 우측 패널 ─────────────────────────────────────────── */}
      {sel && (
        <div className="pointer-events-none absolute right-[384px] top-[4.75rem] z-10 w-[272px]">
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
                  <img src="/logo.png" alt="" width={14} height={14} className="h-[14px] w-[14px]" />
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
      {/* 채팅 패널이 상시 고정(360px + 여백)이라 그만큼 항상 비켜 준다 */}
      <div className="pointer-events-none absolute bottom-0 left-0 right-[372px] z-20 p-3">
        <div className="glass glass-live pointer-events-auto mx-auto max-w-4xl px-4 py-3">
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
  /** r = 상주(야간) 비교용, o5 = 65세 이상, h5 = 30년이상 노후주택 */
  r1?: number;
  r5?: number;
  o5?: number;
  h5?: number;
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
  /** 행정동 코드 — 동 이름이 겹치므로 공간질의는 이 코드로 매칭한다 */
  cds: string[];
  cdi: number[];
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
  /** 주간 보정 인구. 순위를 정하는 건 이 값이다. */
  popd: number;
  old: number;
  age: number | null;
  /** occlusion 기여도(x1000). ot = 12시간 시계열, os = 정적 7개 */
  ot?: number[];
  os?: number[];
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
      top1_pop_day: number;
      top5_pop_day: number;
      top1_pop_old: number;
      top5_pop_old: number;
      top5_old_house: number;
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
  /** 왼쪽에서 고른 행정동. 경계 강조와 화면 이동에 쓴다. */
  picked: Picked | null;
  onPick: (k: number, lon: number, lat: number) => void;
  onReady: (m: MlMap) => void;
};

function FireMap({ root, cells, values, fires, ymd, hour, dailyPng, picked, onPick, onReady }: MapProps) {
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

        // ── 행정경계 ──────────────────────────────────────────
        // 위험도만 깔려 있으면 "여기가 어디지"가 안 된다. 경계와 지명이 있어야
        // 화면이 읽힌다. 동 경계는 6.6MB(gzip 1.3MB)라 지도 구축 뒤 비동기로 붙인다.
        m.addSource("adm", { type: "geojson", data: emptyFC() });
        m.addLayer({
          id: "adm-line",
          type: "line",
          source: "adm",
          paint: {
            "line-color": "#cbd5e1",
            // 전국 축척에서 3,559개 경계를 다 진하게 그리면 지도가 그물이 된다.
            "line-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.18, 9, 0.4, 12, 0.6],
            "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.4, 10, 0.8, 13, 1.4],
          },
        });
        m.addLayer({
          id: "adm-sel-fill",
          type: "fill",
          source: "adm",
          filter: ["==", ["get", "cd"], ""],
          paint: { "fill-color": "#38bdf8", "fill-opacity": 0.16 },
        });
        m.addLayer({
          id: "adm-sel-line",
          type: "line",
          source: "adm",
          filter: ["==", ["get", "cd"], ""],
          paint: { "line-color": "#38bdf8", "line-width": 2.4, "line-opacity": 0.95 },
        });
        m.addLayer({
          id: "adm-label",
          type: "symbol",
          source: "adm",
          minzoom: 8.5,
          layout: {
            "text-field": ["get", "nm"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 9, 10, 13, 13],
            "text-font": ["Noto Sans Regular"],
            "text-allow-overlap": false,
          },
          paint: {
            "text-color": "#e2e8f0",
            "text-halo-color": "#0f172a",
            "text-halo-width": 1.4,
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

  // 행정경계 — 6.6MB 라 지도가 뜬 뒤에 붙인다. 첫 화면을 이것 때문에 기다리게 하지 않는다.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    let dead = false;
    loadAdm().then((j) => {
      if (dead || !j) return;
      (m.getSource("adm") as any)?.setData?.(j);
    });
    return () => {
      dead = true;
    };
  }, [ready]);

  // 선택한 동 강조 + 화면 이동
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const cd = picked?.cd ?? "";
    for (const id of ["adm-sel-fill", "adm-sel-line"]) {
      if (m.getLayer(id)) m.setFilter(id, ["==", ["get", "cd"], cd]);
    }
    if (!picked) return;
    const [w, s, e, n] = picked.b;
    // 좌우 패널이 지도를 가리므로 그만큼 비켜서 맞춘다. 다만 패딩 합이 컨테이너를
    // 넘으면 fitBounds 가 계산을 포기하고 maxZoom 으로 붙어 버린다(격자 픽셀만 보인다).
    // 좁은 창에서도 그런 일이 없게 남는 폭을 보장한다.
    const cw = m.getContainer().clientWidth || 1280;
    const ch = m.getContainer().clientHeight || 720;
    // 비율로 줄이면 넓은 창에서도 패딩이 패널 폭보다 작아져 고른 동이 패널 뒤로
    // 숨는다. 평소에는 패널 폭 그대로 쓰고, 창이 좁을 때만 상한을 건다.
    m.fitBounds(
      [
        [w, s],
        [e, n],
      ],
      {
        padding: {
          top: Math.min(90, ch * 0.15),
          bottom: Math.min(120, ch * 0.2),
          left: Math.min(310, cw * 0.3),
          right: Math.min(400, cw * 0.32),
        },
        // 동 하나는 거의 항상 이 상한에 걸린다. 사실상 표시 배율이라 주변
        // 지명이 같이 보이는 값으로 잡는다.
        maxZoom: 10.6,
        duration: 700,
      }
    );
  }, [ready, picked]);

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
