/**
 * 공간질의 — 챗봇이 화면 밖 지역을 물어도 답할 수 있게 한다.
 *
 * 왜 클라이언트에서 하는가
 *   cells.json(격자→행정동)과 values.json(시각별 격자 위험도)이 이미 브라우저에
 *   올라와 있다. 서버로 다시 물으면 왕복만 늘고 답은 같다. Gemini 함수호출을 쓰면
 *   한 번 더 왕복해야 하는데 지금 예산이 25초라 그럴 여유가 없다.
 *   그래서 질문에서 지역을 찾아 **미리 계산해 컨텍스트에 실어 보낸다.**
 *
 * 두 모드가 서로 다른 자료를 쓴다
 *   사례일 상세  cells.json + values.json 으로 격자에서 직접 집계
 *   전 기간 737일  격자 단위 값이 없어(수십 GB) 51번이 행정동으로 접어 둔
 *                  region_daily.json 을 읽는다. 그래서 시군구·시도 단위까지만 답한다.
 */

export type AdmIndexLite = Record<string, Record<string, { cd: string; nm: string }[]>>;

export type RegionHit = {
  /** 매칭된 이름 (예: "경상북도 의성군", "의성군 금성면") */
  label: string;
  level: "sido" | "sgg" | "dong";
  codes: Set<string>;
};

const SIDO_ALIAS: Record<string, string> = {
  서울: "서울특별시", 부산: "부산광역시", 대구: "대구광역시", 인천: "인천광역시",
  광주: "광주광역시", 대전: "대전광역시", 울산: "울산광역시", 세종: "세종특별자치시",
  경기: "경기도", 강원: "강원특별자치도", 충북: "충청북도", 충남: "충청남도",
  전북: "전북특별자치도", 전남: "전라남도", 경북: "경상북도", 경남: "경상남도",
  제주: "제주특별자치도",
};

/** 질문 문자열에서 지역을 찾는다. 가장 좁은 단위(동 > 시군구 > 시도)를 우선한다. */
export function resolveRegion(q: string, idx: AdmIndexLite): RegionHit | null {
  const s = q.replace(/\s+/g, "");
  let dong: RegionHit | null = null;
  let sgg: RegionHit | null = null;
  let sido: RegionHit | null = null;

  for (const sd of Object.keys(idx)) {
    const sdHit = s.includes(sd) || Object.entries(SIDO_ALIAS).some(([a, full]) => full === sd && s.includes(a));
    for (const sg of Object.keys(idx[sd])) {
      const sgHit = s.includes(sg);
      for (const d of idx[sd][sg]) {
        // 동은 이름이 겹치므로(삼성동 등) 상위 지명이 함께 언급됐을 때만 확정한다.
        if (s.includes(d.nm) && (sgHit || sdHit || !dong)) {
          const exact = sgHit || sdHit;
          if (!dong || exact) {
            dong = { label: `${sg} ${d.nm}`, level: "dong", codes: new Set([d.cd]) };
            if (exact) return dong;
          }
        }
      }
      if (sgHit && !sgg) {
        sgg = { label: `${sd} ${sg}`, level: "sgg", codes: new Set(idx[sd][sg].map((d) => d.cd)) };
      }
    }
    if (sdHit && !sido) {
      const codes = new Set<string>();
      for (const sg of Object.keys(idx[sd])) idx[sd][sg].forEach((d) => codes.add(d.cd));
      sido = { label: sd, level: "sido", codes };
    }
  }
  return dong ?? sgg ?? sido;
}

export type SpatialAnswer = {
  region: string;
  level: string;
  cells: number;
  /** 그 지역에서 가장 위험한 격자의 전국 상위 % (작을수록 위험) */
  bestTopPct: number | null;
  medianTopPct: number | null;
  /** 전국 상위 1% / 5% 안에 든 격자 수 */
  inTop1: number;
  inTop5: number;
  /** 그 지역 전체 격자의 상주인구 합 */
  population: number;
  /** 위험도가 계산된(=상위 구간에 든) 격자만의 인구 합 */
  populationRanked: number;
  /** 지역 안에서 위험한 순으로 행정동 (시도·시군구 질의일 때만) */
  worst: { nm: string; topPct: number; pop: number }[];
};

type Cells = {
  nms: string[]; nmi: number[];
  cds: string[]; cdi: number[];
  pop: number[];
};
type HourValues = { i: number[]; top: number[] };

/**
 * 지역 코드 집합에 대해 현재 시각 위험도를 집계한다.
 * values.hours[hour] 는 상위 구간 격자만 담고 있으므로(전체가 아님)
 * 여기서 나오는 "가장 위험한 격자"는 그 범위 안에서의 값이다.
 */
export function queryRegion(
  hit: RegionHit,
  cells: Cells,
  hv: HourValues | null,
  topScale: number
): SpatialAnswer {
  const idxByCell = new Map<number, number>();
  if (hv) hv.i.forEach((cellIdx, j) => idxByCell.set(cellIdx, j));

  const tops: number[] = [];
  const perDong = new Map<string, { nm: string; best: number; pop: number }>();
  let nCells = 0;
  let pop = 0;
  let popRanked = 0;

  for (let k = 0; k < cells.cdi.length; k++) {
    const cd = cells.cds[cells.cdi[k]];
    if (!hit.codes.has(cd)) continue;
    nCells++;
    pop += cells.pop[k] || 0;
    const j = idxByCell.get(k);
    if (j === undefined) continue;
    // values.json 은 상위 구간 격자만 담는다(전체가 아님). 그래서 인구를 두 벌
    // 센다. 하나만 주면 "의성군 인구 1.2만"처럼 실제(약 5만)와 어긋나 보인다.
    popRanked += cells.pop[k] || 0;
    const t = hv!.top[j] / topScale;
    tops.push(t);
    const nm = cells.nms[cells.nmi[k]] || "";
    const cur = perDong.get(cd);
    if (!cur || t < cur.best) perDong.set(cd, { nm, best: t, pop: cells.pop[k] || 0 });
  }

  tops.sort((a, b) => a - b);
  const worst = [...perDong.values()]
    .sort((a, b) => a.best - b.best)
    .slice(0, 5)
    .map((d) => ({ nm: d.nm, topPct: Math.round(d.best * 100) / 100, pop: Math.round(d.pop) }));

  return {
    region: hit.label,
    level: hit.level,
    cells: nCells,
    bestTopPct: tops.length ? Math.round(tops[0] * 100) / 100 : null,
    medianTopPct: tops.length ? Math.round(tops[Math.floor(tops.length / 2)] * 100) / 100 : null,
    inTop1: tops.filter((t) => t <= 1).length,
    inTop5: tops.filter((t) => t <= 5).length,
    population: Math.round(pop),
    populationRanked: Math.round(popRanked),
    worst: hit.level === "dong" ? [] : worst,
  };
}


// ── 전 기간 모드 ─────────────────────────────────────────────────────
// 56번이 만든 region_daily.json. 격자가 없으므로 지역 단위로만 답한다.

export type RegionDaily = {
  hour: number;
  sidos: string[];
  sggs: string[];
  days: Record<string, {
    sido: number[][];
    sgg: number[][];
    top: [string, string, number][];
  }>;
};

export type TimelineAnswer = {
  region: string;
  level: string;
  date: string;
  bestTopPct: number | null;
  inTop1: number;
  inTop5: number;
  popDay: number;
  popOld: number;
  /** 그날 전국에서 가장 위험한 행정동 (지역 매칭이 없을 때도 준다) */
  nationTop: { nm: string; sgg: string; topPct: number }[];
};

/**
 * 전 기간 모드 질의. hit 이 null 이면 그날 전국 상위 동만 돌려준다 —
 * "오늘 어디가 제일 위험해?" 같은 질문이 지역명 없이 들어오기 때문이다.
 */
export function queryTimeline(
  q: string,
  date: string,
  rd: RegionDaily,
  idx: AdmIndexLite
): TimelineAnswer | null {
  const day = rd.days[date];
  if (!day) return null;

  const nationTop = day.top.slice(0, 5).map(([nm, sgg, b]) => ({
    nm, sgg, topPct: Math.round(b) / 10,
  }));

  const hit = resolveRegion(q, idx);
  let label = "전국";
  let level = "nation";
  let row: number[] | undefined;

  if (hit) {
    // region_daily 는 시도·시군구까지만 있다. 동을 물으면 그 동이 속한
    // 시군구로 올려서 답하고, 라벨에 그 사실을 드러낸다.
    const sggName = hit.level === "sido" ? null : hit.label.split(" ").slice(0, 2).join(" ");
    if (hit.level === "sido") {
      const i = rd.sidos.indexOf(hit.label);
      row = day.sido.find((r) => r[0] === i);
      label = hit.label;
      level = "sido";
    } else {
      const i = rd.sggs.findIndex((s) => sggName && s.endsWith(sggName.split(" ").pop() || ""));
      row = day.sgg.find((r) => r[0] === i);
      label = i >= 0 ? rd.sggs[i] : hit.label;
      level = hit.level === "dong" ? "sgg(동 단위 없음)" : "sgg";
    }
  }

  return {
    region: label,
    level,
    date,
    bestTopPct: row ? Math.round(row[1]) / 10 : null,
    inTop1: row ? row[2] : 0,
    inTop5: row ? row[3] : 0,
    popDay: row ? row[4] : 0,
    popOld: row ? row[5] : 0,
    nationTop,
  };
}
