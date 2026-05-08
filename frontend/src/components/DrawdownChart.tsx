import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import type { EquityCurve } from "../api";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type Props = {
  curves: { iter: number; verdict: string; data: EquityCurve }[];
  highlightIter?: number;
};

const WINDOW_COLORS = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6"];

function drawdownSeries(equity: number[]): number[] {
  const out = new Array<number>(equity.length);
  let peak = -Infinity;
  for (let i = 0; i < equity.length; i++) {
    if (equity[i] > peak) peak = equity[i];
    out[i] = peak > 0 ? equity[i] / peak - 1 : 0;
  }
  return out;
}

const maxDD = (dd: number[]): number => dd.reduce((m, v) => (v < m ? v : m), 0);

export function DrawdownChart({ curves, highlightIter }: Props) {
  if (curves.length === 0) {
    return <div className="text-slate-500 italic">no equity yet</div>;
  }
  const single = curves.length === 1;
  const traces: any[] = [];
  const cutoffs: string[] = [];

  for (const c of curves) {
    const windows = c.data.windows && c.data.windows.length > 0
      ? c.data.windows
      : [{
          window: 0,
          timestamp: c.data.timestamp,
          equity: c.data.equity,
          benchmark: c.data.benchmark,
          split_cutoff: c.data.split_cutoff,
        }];

    for (const w of windows) {
      const dd = drawdownSeries(w.equity);
      const md = maxDD(dd);
      const color = single ? WINDOW_COLORS[w.window % WINDOW_COLORS.length] : undefined;
      traces.push({
        x: w.timestamp,
        y: dd,
        mode: "lines",
        type: "scatter",
        name: single
          ? (windows.length > 1
              ? `w${w.window} — DD ${(md * 100).toFixed(1)}%`
              : `strategy — DD ${(md * 100).toFixed(1)}%`)
          : `iter ${c.iter} (${c.verdict}) — DD ${(md * 100).toFixed(1)}%`,
        legendgroup: single ? `w${w.window}` : `i${c.iter}`,
        // Fill only when there's exactly one window in single-strategy mode,
        // otherwise overlapping fills become unreadable.
        fill: single && windows.length === 1 ? "tozeroy" : undefined,
        fillcolor: single && windows.length === 1 ? "rgba(239,68,68,0.18)" : undefined,
        line: {
          color,
          width: c.iter === highlightIter ? 2.2 : single ? 1.6 : 1,
        },
      });
      if (single && windows.length === 1) {
        const ddBench = drawdownSeries(w.benchmark);
        const mdBench = maxDD(ddBench);
        traces.push({
          x: w.timestamp,
          y: ddBench,
          mode: "lines",
          type: "scatter",
          name: `buy & hold — DD ${(mdBench * 100).toFixed(1)}%`,
          line: { color: "#94a3b8", dash: "dot", width: 1.5 },
        });
      }
      if (w.split_cutoff) cutoffs.push(w.split_cutoff);
    }
  }

  return (
    <Plot
      data={traces}
      style={{ width: "100%", height: 280 }}
      useResizeHandler
      config={{ displaylogo: false, responsive: true }}
      layout={{
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#cbd5e1", size: 11 },
        margin: { t: 10, b: 40, l: 60, r: 10 },
        xaxis: { gridcolor: "#334155" },
        yaxis: {
          title: { text: "drawdown" },
          gridcolor: "#334155",
          tickformat: ".0%",
          rangemode: "nonpositive",
        },
        legend: { orientation: "h", y: -0.22 },
        shapes: cutoffs.map((c) => ({
          type: "line" as const,
          x0: c,
          x1: c,
          yref: "paper" as const,
          y0: 0,
          y1: 1,
          line: { color: "#ef4444", dash: "dash" as const, width: 1 },
        })),
      }}
    />
  );
}
