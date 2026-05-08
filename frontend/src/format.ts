export const fmt = (x: number | null | undefined, n = 4): string =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(n);

export const fmtPct = (x: number | null | undefined, n = 2): string =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : `${(Number(x) * 100).toFixed(n)}%`;

export const probabilityClass = (p: number | null | undefined): string => {
  if (p === null || p === undefined || Number.isNaN(p)) return "text-slate-500";
  if (p >= 0.95) return "text-emerald-400 font-semibold";
  if (p >= 0.5) return "text-amber-400";
  return "text-rose-400";
};

export const verdictClass = (v: string): string => {
  const k = v.toLowerCase();
  if (k === "keep" || k === "baseline") return "bg-emerald-500/10 text-emerald-400";
  if (k === "revert") return "bg-rose-500/10 text-rose-400";
  if (k === "error") return "bg-amber-500/10 text-amber-400";
  if (k.startsWith("lookahead_bug")) return "bg-amber-600/20 text-amber-300 font-semibold";
  return "bg-slate-500/10 text-slate-400";
};
