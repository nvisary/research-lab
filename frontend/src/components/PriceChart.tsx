import { useEffect, useMemo, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import { api, type OhlcvPayload, type TradeRow } from "../api";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type Props = {
  strategy: string;
  iter: number | null;
  symbols: string[];        // available symbols (from best.symbols)
  start: string;
  end: string;
  tf: string;
};

export function PriceChart({ strategy, iter, symbols, start, end, tf }: Props) {
  const [activeSymbol, setActiveSymbol] = useState(symbols[0] ?? "BTCUSDT");
  const [ohlcv, setOhlcv] = useState<OhlcvPayload | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!activeSymbol) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .ohlcv(activeSymbol, start, end, tf)
      .then((d) => {
        if (!cancelled) setOhlcv(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e?.message ?? e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSymbol, start, end, tf]);

  useEffect(() => {
    if (iter === null) {
      setTrades([]);
      return;
    }
    let cancelled = false;
    api
      .trades(strategy, iter)
      .then((p) => {
        if (!cancelled) setTrades(p.rows);
      })
      .catch(() => {
        if (!cancelled) setTrades([]);
      });
    return () => {
      cancelled = true;
    };
  }, [strategy, iter]);

  // Filter trades to active symbol.
  const symTrades = useMemo(
    () => trades.filter((t) => t.symbol === activeSymbol),
    [trades, activeSymbol]
  );

  if (loading) return <div className="text-slate-500 italic">loading price…</div>;
  if (error) return <div className="text-rose-400">price load failed: {error}</div>;
  if (!ohlcv) return null;

  const traces: any[] = [
    {
      x: ohlcv.timestamp,
      y: ohlcv.close,
      mode: "lines",
      type: "scatter",
      name: `${activeSymbol} close`,
      line: { color: "#94a3b8", width: 1 },
      hovertemplate: "%{x}<br>close=%{y:.2f}<extra></extra>",
    },
  ];

  // Trade markers: entry triangle-up, exit triangle-down. Color by sign
  // of return_pct. Connecting line between entry and exit, faint.
  if (symTrades.length > 0) {
    const entryX: string[] = [];
    const entryY: number[] = [];
    const entryColors: string[] = [];
    const entryHovers: string[] = [];

    const exitX: string[] = [];
    const exitY: number[] = [];
    const exitColors: string[] = [];
    const exitHovers: string[] = [];

    // One connecting segment per trade — emit as a single trace with
    // None breaks so Plotly draws disjoint line segments.
    const segX: (string | null)[] = [];
    const segY: (number | null)[] = [];

    for (const t of symTrades) {
      const winning = t.pnl_quote > 0;
      // Long-entry green, short-entry purple. Exit color matches PnL sign:
      // green if profitable, red if not.
      const isLong = String(t.direction).toLowerCase().startsWith("long");
      const entryColor = isLong ? "#10b981" : "#a855f7";
      const exitColor = winning ? "#10b981" : "#ef4444";
      const segColor = winning ? "#10b98155" : "#ef444455";

      const pnlStr = `pnl ${(t.return_pct * 100).toFixed(2)}% ($${t.pnl_quote.toFixed(2)})`;
      const durStr = `${t.duration_hours.toFixed(1)}h`;
      const baseHover =
        `${t.direction} ${activeSymbol}<br>` +
        `entry ${t.entry_time}<br>@ ${t.entry_price.toFixed(2)}<br>` +
        `exit ${t.exit_time}<br>@ ${t.exit_price.toFixed(2)}<br>` +
        `${pnlStr} • ${durStr}<extra></extra>`;

      entryX.push(t.entry_time);
      entryY.push(t.entry_price);
      entryColors.push(entryColor);
      entryHovers.push(baseHover);

      exitX.push(t.exit_time);
      exitY.push(t.exit_price);
      exitColors.push(exitColor);
      exitHovers.push(baseHover);

      segX.push(t.entry_time, t.exit_time, null);
      segY.push(t.entry_price, t.exit_price, null);
      // Note: per-segment colour is not directly supported in a single
      // line trace; we render one global semi-transparent green line for
      // wins and one red for losses below.
    }

    // Two separate line traces for win/loss colouring of segments.
    const winSegX: (string | null)[] = [];
    const winSegY: (number | null)[] = [];
    const lossSegX: (string | null)[] = [];
    const lossSegY: (number | null)[] = [];
    for (const t of symTrades) {
      const arrX = t.pnl_quote > 0 ? winSegX : lossSegX;
      const arrY = t.pnl_quote > 0 ? winSegY : lossSegY;
      arrX.push(t.entry_time, t.exit_time, null);
      arrY.push(t.entry_price, t.exit_price, null);
    }
    if (winSegX.length > 0) {
      traces.push({
        x: winSegX, y: winSegY,
        mode: "lines", type: "scatter", name: "winning trades",
        line: { color: "#10b98166", width: 1 },
        hoverinfo: "skip",
        showlegend: true,
      });
    }
    if (lossSegX.length > 0) {
      traces.push({
        x: lossSegX, y: lossSegY,
        mode: "lines", type: "scatter", name: "losing trades",
        line: { color: "#ef444466", width: 1 },
        hoverinfo: "skip",
        showlegend: true,
      });
    }

    traces.push({
      x: entryX, y: entryY,
      mode: "markers", type: "scatter", name: "entries",
      marker: {
        symbol: "triangle-up", size: 10,
        color: entryColors, line: { color: "#0f172a", width: 1 },
      },
      hovertemplate: entryHovers,
      hoverinfo: "text",
    });
    traces.push({
      x: exitX, y: exitY,
      mode: "markers", type: "scatter", name: "exits",
      marker: {
        symbol: "triangle-down", size: 10,
        color: exitColors, line: { color: "#0f172a", width: 1 },
      },
      hovertemplate: exitHovers,
      hoverinfo: "text",
    });
  }

  return (
    <div>
      {symbols.length > 1 && (
        <div className="mb-2 flex gap-2 items-center">
          <span className="text-xs text-slate-400">symbol:</span>
          <select
            className="bg-slate-800 text-slate-200 text-xs rounded px-2 py-1 border border-slate-700"
            value={activeSymbol}
            onChange={(e) => setActiveSymbol(e.target.value)}
          >
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="text-xs text-slate-500">
            {symTrades.length} trade{symTrades.length === 1 ? "" : "s"} on this symbol
          </span>
        </div>
      )}
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
          yaxis: { title: { text: "price" }, gridcolor: "#334155" },
          legend: { orientation: "h", y: -0.18 },
          hovermode: "closest",
        }}
      />
    </div>
  );
}
