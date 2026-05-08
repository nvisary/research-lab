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

/** equity -> drawdown as a fraction in [-1, 0]: dd_t = equity_t / max(equity_{0..t}) - 1 */
function drawdownSeries(equity: number[]): number[] {
  const out = new Array<number>(equity.length);
  let peak = -Infinity;
  for (let i = 0; i < equity.length; i++) {
    if (equity[i] > peak) peak = equity[i];
    out[i] = peak > 0 ? equity[i] / peak - 1 : 0;
  }
  return out;
}

function maxDrawdown(dd: number[]): number {
  let m = 0;
  for (const v of dd) if (v < m) m = v;
  return m; // negative
}

export function DrawdownChart({ curves, highlightIter }: Props) {
  if (curves.length === 0) {
    return <div className="text-slate-500 italic">no equity yet</div>;
  }
  const cutoff = curves[0].data.split_cutoff;
  const single = curves.length === 1;

  const traces: any[] = [];
  for (const c of curves) {
    const dd = drawdownSeries(c.data.equity);
    const maxDD = maxDrawdown(dd);
    traces.push({
      x: c.data.timestamp,
      y: dd,
      mode: "lines",
      type: "scatter",
      name: `iter ${c.iter} (${c.verdict}) — DD ${(maxDD * 100).toFixed(1)}%`,
      // For single-series view, fill the area to make the underwater plot obvious.
      fill: single ? "tozeroy" : undefined,
      fillcolor: single ? "rgba(239, 68, 68, 0.18)" : undefined,
      line: {
        color: single ? "#ef4444" : undefined,
        width: c.iter === highlightIter ? 2.2 : single ? 1.5 : 1,
      },
    });
  }
  if (single) {
    const ddBench = drawdownSeries(curves[0].data.benchmark);
    const maxDDBench = maxDrawdown(ddBench);
    traces.push({
      x: curves[0].data.timestamp,
      y: ddBench,
      mode: "lines",
      type: "scatter",
      name: `buy & hold — DD ${(maxDDBench * 100).toFixed(1)}%`,
      line: { color: "#94a3b8", dash: "dot", width: 1.5 },
    });
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
        margin: { t: 10, b: 36, l: 60, r: 10 },
        xaxis: { gridcolor: "#334155" },
        yaxis: {
          title: { text: "drawdown" },
          gridcolor: "#334155",
          tickformat: ".0%",
          rangemode: "nonpositive",
        },
        legend: { orientation: "h", y: -0.22 },
        shapes: cutoff
          ? [
              {
                type: "line",
                x0: cutoff,
                x1: cutoff,
                yref: "paper",
                y0: 0,
                y1: 1,
                line: { color: "#ef4444", dash: "dash", width: 1 },
              },
            ]
          : [],
      }}
    />
  );
}
