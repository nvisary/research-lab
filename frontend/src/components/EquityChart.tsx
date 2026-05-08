import createPlotlyComponent from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import type { EquityCurve } from "../api";

const Plot = createPlotlyComponent(Plotly);

type Props = {
  curves: { iter: number; verdict: string; data: EquityCurve }[];
  highlightIter?: number;
};

export function EquityChart({ curves, highlightIter }: Props) {
  if (curves.length === 0) {
    return <div className="text-slate-500 italic">no equity yet</div>;
  }
  const cutoff = curves[0].data.split_cutoff;
  const single = curves.length === 1;

  const traces: any[] = [];
  for (const c of curves) {
    traces.push({
      x: c.data.timestamp,
      y: c.data.equity,
      mode: "lines",
      type: "scatter",
      name: `iter ${c.iter} (${c.verdict})`,
      line: {
        width: c.iter === highlightIter ? 2.5 : single ? 2 : 1,
        color: single ? "#60a5fa" : undefined,
      },
    });
  }
  if (single) {
    traces.push({
      x: curves[0].data.timestamp,
      y: curves[0].data.benchmark,
      mode: "lines",
      type: "scatter",
      name: "buy & hold",
      line: { color: "#94a3b8", dash: "dot", width: 1.5 },
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
        margin: { t: 10, b: 36, l: 60, r: 10 },
        xaxis: { gridcolor: "#334155" },
        yaxis: { title: { text: "equity" }, gridcolor: "#334155" },
        legend: { orientation: "h", y: -0.18 },
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
        annotations: cutoff
          ? [
              {
                x: cutoff,
                yref: "paper",
                y: 1.04,
                text: "OOS →",
                showarrow: false,
                font: { color: "#ef4444", size: 10 },
              },
            ]
          : [],
      }}
    />
  );
}
