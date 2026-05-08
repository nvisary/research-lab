import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import type { EquityCurve, EquityWindow } from "../api";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type Props = {
  curves: { iter: number; verdict: string; data: EquityCurve }[];
  highlightIter?: number;
};

const WINDOW_COLORS = ["#60a5fa", "#34d399", "#f59e0b", "#a78bfa", "#f472b6", "#22d3ee"];

export function EquityChart({ curves, highlightIter }: Props) {
  if (curves.length === 0) {
    return <div className="text-slate-500 italic">no equity yet</div>;
  }
  const single = curves.length === 1;
  const traces: any[] = [];
  const cutoffs: string[] = [];
  // Window boundaries are derived from the FIRST curve's window timestamps.
  // In overlay mode all curves share the same WF schedule, so the first one
  // is representative.
  const firstWindows: EquityWindow[] | null =
    curves[0]?.data.windows && curves[0].data.windows.length > 0
      ? curves[0].data.windows
      : null;

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
      const color = single ? WINDOW_COLORS[w.window % WINDOW_COLORS.length] : undefined;
      traces.push({
        x: w.timestamp,
        y: w.equity,
        mode: "lines",
        type: "scatter",
        name: single
          ? (windows.length > 1 ? `equity w${w.window}` : `equity`)
          : `iter ${c.iter} (${c.verdict})`,
        legendgroup: single ? `w${w.window}` : `i${c.iter}`,
        line: {
          width: c.iter === highlightIter ? 2.5 : single ? 2 : 1,
          color,
        },
      });
      if (single) {
        traces.push({
          x: w.timestamp,
          y: w.benchmark,
          mode: "lines",
          type: "scatter",
          name: windows.length > 1 ? `b&h w${w.window}` : "buy & hold",
          legendgroup: `w${w.window}`,
          showlegend: w.window === 0 || windows.length === 1,
          line: { color: "#94a3b8", dash: "dot", width: 1 },
        });
      }
      if (w.split_cutoff) cutoffs.push(w.split_cutoff);
    }
  }

  // Window boundaries (solid faint lines) so the user can see exactly where
  // each WF window starts/ends. Internal cutoffs (red dashed) are train→OOS
  // splits inside each window — different concept.
  const shapes: any[] = [];
  const annotations: any[] = [];
  if (firstWindows && firstWindows.length > 1) {
    firstWindows.forEach((w, i) => {
      const start = w.timestamp[0];
      // Faint background shade for the train slice of each window so users
      // see "this slab is train, after the red line is OOS, the next slab
      // is the next window".
      if (w.split_cutoff) {
        shapes.push({
          type: "rect" as const,
          xref: "x" as const,
          yref: "paper" as const,
          x0: start,
          x1: w.split_cutoff,
          y0: 0,
          y1: 1,
          fillcolor: "rgba(148, 163, 184, 0.04)",  // slate-400 @ 4%
          line: { width: 0 },
          layer: "below" as const,
        });
      }
      // Solid window-boundary line at the START of each window (skip i=0 since
      // it's the chart left edge already).
      if (i > 0) {
        shapes.push({
          type: "line" as const,
          xref: "x" as const,
          yref: "paper" as const,
          x0: start, x1: start, y0: 0, y1: 1,
          line: { color: "#475569", width: 1 },  // slate-600
        });
      }
      // Window label centered horizontally on the window
      const mid = w.timestamp[Math.floor(w.timestamp.length / 2)];
      annotations.push({
        x: mid, xref: "x" as const,
        y: 1.04, yref: "paper" as const,
        text: `w${w.window}`,
        showarrow: false,
        font: { color: WINDOW_COLORS[w.window % WINDOW_COLORS.length], size: 11 },
      });
    });
  }

  // Train→OOS cutoff lines (red, dashed). These are INSIDE each window.
  cutoffs.forEach((c) => shapes.push({
    type: "line" as const,
    xref: "x" as const,
    x0: c, x1: c,
    yref: "paper" as const, y0: 0, y1: 1,
    line: { color: "#ef4444", dash: "dash" as const, width: 1 },
  }));
  if (cutoffs.length === 1) {
    annotations.push({
      x: cutoffs[0], xref: "x" as const,
      y: 1.04, yref: "paper" as const,
      text: "train | OOS",
      showarrow: false,
      font: { color: "#ef4444", size: 10 },
    });
  }

  return (
    <Plot
      data={traces}
      style={{ width: "100%", height: 380 }}
      useResizeHandler
      config={{ displaylogo: false, responsive: true }}
      layout={{
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#cbd5e1", size: 11 },
        margin: { t: 24, b: 40, l: 60, r: 10 },
        xaxis: { gridcolor: "#334155" },
        yaxis: { title: { text: "equity" }, gridcolor: "#334155" },
        legend: { orientation: "h", y: -0.18 },
        shapes,
        annotations,
      }}
    />
  );
}
