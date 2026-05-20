/**
 * SweepHeatmap — color matrix of (symbol × period) cells.
 *
 * Diverging palette for signed metrics (Sharpe, total_return), sequential
 * for one-sided metrics (max_dd, n_trades). Click a cell to drill into
 * the equity drawer. Hover shows a compact metric block.
 */
import { useMemo, useState } from "react";
import type { SweepCellRow, SweepManifest } from "../api";
import { fmt, fmtPct } from "../format";

export type SweepMetric =
  | "sharpe"
  | "total_return"
  | "max_dd"
  | "n_trades"
  | "pct_time_in_position"
  | "profit_factor";

const METRIC_OPTIONS: { value: SweepMetric; label: string; signed: boolean }[] = [
  { value: "sharpe", label: "Sharpe", signed: true },
  { value: "total_return", label: "Total return", signed: true },
  { value: "max_dd", label: "MaxDD", signed: false },
  { value: "n_trades", label: "n_trades", signed: false },
  { value: "pct_time_in_position", label: "TiP %", signed: false },
  { value: "profit_factor", label: "Profit factor", signed: true },
];

/* Diverging palette: red (loss) → grey → green (win). */
function divergeColor(v: number, max: number): string {
  if (max <= 0) return "#1f2937";
  const t = Math.max(-1, Math.min(1, v / max));
  if (t === 0) return "#1f2937";
  if (t > 0) {
    const a = 0.15 + 0.65 * t;
    return `rgba(52,211,153,${a.toFixed(3)})`;
  } else {
    const a = 0.15 + 0.65 * -t;
    return `rgba(248,113,113,${a.toFixed(3)})`;
  }
}

/* Sequential palette: amber, lighter = larger (worse for DD, more for n_trades). */
function seqColor(v: number, max: number): string {
  if (max <= 0) return "#1f2937";
  const t = Math.max(0, Math.min(1, v / max));
  const a = 0.10 + 0.65 * t;
  return `rgba(251,191,36,${a.toFixed(3)})`;
}

function valueLabel(metric: SweepMetric, v: number | null): string {
  if (v === null || v === undefined) return "—";
  if (metric === "n_trades") return String(Math.round(v));
  if (metric === "total_return" || metric === "max_dd"
      || metric === "pct_time_in_position") {
    return fmtPct(v, 1);
  }
  return fmt(v, 2);
}

type Props = {
  manifest: SweepManifest;
  rows: SweepCellRow[];
  metric: SweepMetric;
  onMetricChange: (m: SweepMetric) => void;
  onCellClick?: (cell: SweepCellRow) => void;
};

export function SweepHeatmap({
  manifest, rows, metric, onMetricChange, onCellClick,
}: Props) {
  const periods = manifest.periods.map((p) => p.label);
  const symbols = manifest.symbols;

  const cellByKey = useMemo(() => {
    const m = new Map<string, SweepCellRow>();
    for (const r of rows) m.set(`${r.symbol}::${r.period}`, r);
    return m;
  }, [rows]);

  const metricMeta = METRIC_OPTIONS.find((o) => o.value === metric)!;
  const values = rows
    .map((r) => r[metric] as number | null)
    .filter((v): v is number => v !== null && !Number.isNaN(v));
  const absMax = Math.max(
    1e-9, ...values.map((v) => Math.abs(v)),
  );

  const [hover, setHover] = useState<SweepCellRow | null>(null);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-slate-400">metric</span>
        {METRIC_OPTIONS.map((o) => (
          <button
            key={o.value}
            onClick={() => onMetricChange(o.value)}
            className={`px-2 py-0.5 text-xs rounded border font-mono
              ${metric === o.value
                ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                : "border-edge text-slate-400 hover:text-slate-200"}`}
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table
          className="text-xs border-collapse"
          style={{ tableLayout: "fixed" }}
        >
          <thead>
            <tr>
              <th className="sticky left-0 bg-panel z-10 px-2 py-1 text-left text-slate-400">
                symbol
              </th>
              {periods.map((p) => (
                <th
                  key={p}
                  className="px-2 py-1 text-center text-slate-400 font-mono"
                  style={{ minWidth: 90 }}
                >
                  {p}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((sym) => (
              <tr key={sym} className="border-t border-edge/40">
                <td className="sticky left-0 bg-panel z-10 px-2 py-1 text-slate-300 font-mono whitespace-nowrap">
                  {sym}
                </td>
                {periods.map((p) => {
                  const cell = cellByKey.get(`${sym}::${p}`);
                  const v = cell ? (cell[metric] as number | null) : null;
                  const erred = !!cell?.error;
                  let bg = "transparent";
                  if (erred) {
                    bg = "repeating-linear-gradient(45deg, " +
                      "rgba(120,113,108,0.12) 0 4px, transparent 4px 8px)";
                  } else if (v !== null && v !== undefined) {
                    bg = metricMeta.signed
                      ? divergeColor(v, absMax)
                      : seqColor(v, absMax);
                  }
                  return (
                    <td
                      key={p}
                      className="px-2 py-1 text-center font-mono cursor-pointer hover:outline hover:outline-1 hover:outline-slate-300"
                      style={{ background: bg }}
                      onClick={() => cell && onCellClick?.(cell)}
                      onMouseEnter={() => cell && setHover(cell)}
                      onMouseLeave={() => setHover(null)}
                      title={
                        cell
                          ? (erred
                              ? `error: ${cell.error}`
                              : `Sharpe=${fmt(cell.sharpe, 2)} ` +
                                `MaxDD=${fmtPct(cell.max_dd, 1)} ` +
                                `Ret=${fmtPct(cell.total_return, 1)} ` +
                                `n=${cell.n_trades ?? "—"}`)
                          : "no data"
                      }
                    >
                      {erred ? "✗" : valueLabel(metric, v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hover && (
        <div className="text-xs text-slate-400 font-mono">
          <span className="text-slate-200">{hover.symbol}</span>
          {" · "}
          <span className="text-slate-300">{hover.period}</span>
          {hover.error ? (
            <span className="text-rose-400"> · err: {hover.error}</span>
          ) : (
            <>
              {" · Sharpe="}
              <span className="text-slate-200">{fmt(hover.sharpe, 2)}</span>
              {" · MaxDD="}
              <span className="text-slate-200">{fmtPct(hover.max_dd, 2)}</span>
              {" · TotalRet="}
              <span className="text-slate-200">{fmtPct(hover.total_return, 2)}</span>
              {" · n_trades="}
              <span className="text-slate-200">{hover.n_trades ?? "—"}</span>
              {" · TiP="}
              <span className="text-slate-200">{fmtPct(hover.pct_time_in_position, 1)}</span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
