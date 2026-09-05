const STATIC_LABEL: Record<string, string> = {
  P_lgbm: "공간 취약도",
  ndvi: "식생 활력",
  ndmi: "식생 수분",
  hum4d: "4일 습도",
  prcp4d: "4일 강수",
  doy_sin: "계절",
  doy_cos: "계절",
};
const STATIC_KEYS = ["P_lgbm", "ndvi", "ndmi", "hum4d", "prcp4d", "doy_sin", "doy_cos"];

type Props = {
  /** 12시간 시계열 기여도(×1000). 인덱스 0 = t−11h, 11 = t0 */
  ot: number[];
  /** 정적 7개 기여도(×1000). STATIC_KEYS 순서 */
  os: number[];
};

/**
 * 왜 이 격자가 위험한가 — occlusion 기여도.
 *
 * 각 입력을 기준값으로 덮었을 때 출력이 얼마나 떨어지는지를 잰다.
 * 값이 클수록 그 입력이 위험도를 끌어올렸다는 뜻이다. SHAP 이 아니라
 * occlusion 을 쓴 이유는, 이 모델의 입력이 12시간 시계열이라
 * "최근 몇 시간 중 어느 시점이 결정적이었나"가 곧 진화 지휘에 쓰이는 답이기 때문이다.
 */
export default function WhyPanel({ ot, os }: Props) {
  // 계절(doy_sin/cos)은 둘로 쪼개져 있어 따로 보면 해석이 안 된다. 합쳐서 하나로 본다.
  const stat = STATIC_KEYS.slice(0, 5).map((k, i) => ({ k, label: STATIC_LABEL[k], v: os[i] ?? 0 }));
  stat.push({ k: "season", label: "계절", v: (os[5] ?? 0) + (os[6] ?? 0) });

  const all = [...ot.map((v) => Math.abs(v)), ...stat.map((s) => Math.abs(s.v))];
  const max = Math.max(1, ...all);
  const tMax = Math.max(...ot.map((v) => Math.abs(v)));
  const peak = ot.indexOf(ot.reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a), 0));

  const bar = (v: number) => {
    const w = (Math.abs(v) / max) * 100;
    return (
      <span className="relative block h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <span
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${w}%`, background: v >= 0 ? "#fb923c" : "#38bdf8" }}
        />
      </span>
    );
  };

  return (
    <div className="mt-1.5 rounded-lg border border-white/5 bg-black/20 p-2">
      <div className="mb-1.5 text-[10px] leading-relaxed text-slate-400">
        각 입력을 기준값으로 덮었을 때 위험도가 얼마나 내려가는지입니다.
        <b className="text-orange-300"> 주황</b>은 위험을 올린 요인,
        <b className="text-sky-300"> 파랑</b>은 내린 요인입니다.
      </div>

      <div className="mb-0.5 text-[10px] font-medium text-slate-300">최근 12시간</div>
      <div className="mb-1 flex items-end gap-[2px]">
        {ot.map((v, i) => (
          <span
            key={i}
            title={`t${i - 11 === 0 ? "0" : i - 11}h · ${(v / 1000).toFixed(3)}`}
            className="flex-1 rounded-sm"
            style={{
              height: `${Math.max(2, (Math.abs(v) / Math.max(1, tMax)) * 26)}px`,
              background: v >= 0 ? "#fb923c" : "#38bdf8",
              opacity: i === peak ? 1 : 0.55,
            }}
          />
        ))}
      </div>
      <div className="mb-2 flex justify-between text-[9px] text-slate-500">
        <span>−11h</span>
        <span className="text-slate-400">
          {peak === 11 ? "직전 1시간이 결정적" : `${11 - peak}시간 전이 결정적`}
        </span>
        <span>직전</span>
      </div>

      <div className="mb-0.5 text-[10px] font-medium text-slate-300">지역·환경 조건</div>
      {stat
        .slice()
        .sort((a, b) => Math.abs(b.v) - Math.abs(a.v))
        .map((sv) => (
          <div key={sv.k} className="mb-1 flex items-center gap-2">
            <span className="w-14 shrink-0 text-[10px] text-slate-400">{sv.label}</span>
            {bar(sv.v)}
            <span className="tnum w-10 shrink-0 text-right text-[9px] text-slate-500">
              {(sv.v / 1000).toFixed(3)}
            </span>
          </div>
        ))}
    </div>
  );
}
