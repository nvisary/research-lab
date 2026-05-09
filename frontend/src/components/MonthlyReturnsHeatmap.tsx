import { useEffect, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import { api, type MonthlyReturnsPayload } from "../api";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Props = { strategy: string; iter: number | null };

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

  // Build z = pct values, text = formatted strings.
  const z: (number | null)[][] = data.data;
  const text: string[][] = data.data.map((row) =>
    row.map((v) => (v === null ? "" : `${(v * 100).toFixed(1)}%`))
  );

  // Custom diverging colour scale, red↔green at zero.
  const colorscale: [number, string][] = [
    [0.0, "#7f1d1d"],   // red-900
    [0.25, "#dc2626"],  // red-600
    [0.5, "#1e293b"],   // slate-800 (zero)
    [0.75, "#16a34a"],  // green-600
    [1.0, "#14532d"],   // green-900
  ];

  // Symmetric range based on max abs value, capped at 30% so a single
  // outlier doesn't squash the rest of the cells visually.
  const flat = z.flat().filter((v): v is number => v !== null);
  const maxAbs = Math.min(0.30, Math.max(0.05, ...flat.map((v) => Math.abs(v))));

  // Annual rollup row above the heatmap: shown as a separate text strip.
  const yearRowText = data.years.map((y, i) => {
    const yr = data.year_returns[i];
    return yr === null ? `${y}: —` : `${y}: ${(yr * 100).toFixed(1)}%`;
  });

  return (
    <div>
      <div className="mb-2 text-xs text-slate-400">
        {data.n_months} months. Cells are compounded monthly returns; year
        column to the right is the full-year compounded return for months
        present in the data.
      </div>
      <Plot
        data={[
          {
            z,
            x: MONTHS,
            y: data.years.map(String),
            type: "heatmap",
            colorscale,
            zmin: -maxAbs,
            zmax: maxAbs,
            zmid: 0,
            text,
            texttemplate: "%{text}",
            hovertemplate: "%{y} %{x}<br>%{z:.2%}<extra></extra>",
            colorbar: {
              tickformat: ".0%",
              tickfont: { color: "#94a3b8", size: 10 },
              outlinewidth: 0,
              len: 0.85,
            },
          } as any,
        ]}
        style={{ width: "100%", height: 60 + data.years.length * 36 + 60 }}
        useResizeHandler
        config={{ displaylogo: false, responsive: true }}
        layout={{
          paper_bgcolor: "rgba(0,0,0,0)",
          plot_bgcolor: "rgba(0,0,0,0)",
          font: { color: "#cbd5e1", size: 11 },
          margin: { t: 8, b: 30, l: 60, r: 40 },
          xaxis: { side: "top" as any, gridcolor: "#334155" },
          yaxis: { autorange: "reversed", gridcolor: "#334155" },
        }}
      />
      <div className="mt-2 text-xs text-slate-400 grid grid-cols-2 sm:grid-cols-4 gap-1">
        {yearRowText.map((s) => (
          <span key={s} className="px-2 py-0.5 bg-slate-800 rounded">
            {s}
          </span>
        ))}
      </div>
    </div>
  );
}
