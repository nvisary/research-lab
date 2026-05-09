/**
 * Monthly returns heatmap — plain HTML/Tailwind grid.
 *
 * Earlier version used Plotly heatmap, but plotly.js-basic-dist-min
 * does NOT include the heatmap trace type, so Plotly silently fell
 * back to scatter and rendered two data points connected by a line.
 * The plain-HTML implementation avoids that bundle issue, gives us
 * full control over cell styling, and ships ~0KB extra.
 */
import { useEffect, useState } from "react";
import { api, type MonthlyReturnsPayload } from "../api";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Props = { strategy: string; iter: number | null };

/** Map a return in [-cap, +cap] to a CSS background color (red→slate→green). */
function cellColor(ret: number, cap: number): string {
  if (cap <= 0) return "rgb(30, 41, 59)";  // slate-800
  const t = Math.max(-1, Math.min(1, ret / cap));    // [-1, +1]
  if (t === 0) return "rgb(30, 41, 59)";
  if (t > 0) {
    // green: rgb(34, 197, 94) at t=1 → slate-800 at t=0
    const r = Math.round(30 + (34 - 30) * t);
    const g = Math.round(41 + (197 - 41) * t);
    const b = Math.round(59 + (94 - 59) * t);
    return `rgb(${r}, ${g}, ${b})`;
  } else {
    // red: rgb(239, 68, 68) at t=-1 → slate-800 at t=0
    const u = -t;
    const r = Math.round(30 + (239 - 30) * u);
    const g = Math.round(41 + (68 - 41) * u);
    const b = Math.round(59 + (68 - 59) * u);
    return `rgb(${r}, ${g}, ${b})`;
  }
}

export function MonthlyReturnsHeatmap({ strategy, iter }: Props) {
  const [data, setData] = useState<MonthlyReturnsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (iter === null) {
      setData(null);
      return;
    }
    let cancelled = false;
    setError(null);
    setData(null);
    api
      .monthlyReturns(strategy, iter)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e?.message ?? e));
      });
    return () => {
      cancelled = true;
    };
  }, [strategy, iter]);

  if (iter === null) return <em className="text-slate-500">no iteration selected</em>;
  if (error) return <span className="text-rose-400">{error}</span>;
  if (!data) return <span className="text-slate-500">loading…</span>;
  if (data.years.length === 0) {
    return <em className="text-slate-500">not enough months for a heatmap</em>;
  }

  // Symmetric range based on max abs value, capped at 30% so a single
  // outlier doesn't squash the rest of the cells visually.
  const flat = data.data.flat().filter((v): v is number => v !== null);
  const maxAbs = Math.min(0.30, Math.max(0.05, ...flat.map((v) => Math.abs(v))));

  return (
    <div>
      <div className="mb-3 text-xs text-slate-400">
        {data.n_months} months. Cells are compounded monthly returns; year
        column to the right is the full-year compounded return for months
        present in the data. Color scale ±{(maxAbs * 100).toFixed(0)}%.
      </div>

      <div className="overflow-x-auto">
        <table className="text-xs border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="text-slate-500 font-normal text-right px-2 py-1">
                year
              </th>
              {MONTHS.map((m) => (
                <th
                  key={m}
                  className="text-slate-500 font-normal text-center w-14 py-1"
                >
                  {m}
                </th>
              ))}
              <th className="text-slate-500 font-normal text-center w-16 py-1 pl-3">
                year
              </th>
            </tr>
          </thead>
          <tbody>
            {data.years.map((y, i) => (
              <tr key={y}>
                <td className="text-slate-300 text-right px-2 py-1 font-medium">
                  {y}
                </td>
                {data.data[i].map((v, mIdx) => (
                  <td
                    key={mIdx}
                    className="text-center px-1 py-1 rounded w-14 text-slate-100"
                    style={{
                      backgroundColor: v === null
                        ? "rgb(15, 23, 42)"
                        : cellColor(v, maxAbs),
                    }}
                    title={v === null ? "—" : `${(v * 100).toFixed(2)}%`}
                  >
                    {v === null ? "" : `${(v * 100).toFixed(1)}%`}
                  </td>
                ))}
                <td
                  className="text-center px-2 py-1 rounded text-slate-100 ml-2 font-semibold"
                  style={{
                    backgroundColor: data.year_returns[i] === null
                      ? "rgb(15, 23, 42)"
                      : cellColor(data.year_returns[i] as number, maxAbs * 4),
                  }}
                >
                  {data.year_returns[i] === null
                    ? "—"
                    : `${((data.year_returns[i] as number) * 100).toFixed(1)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
