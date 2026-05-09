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

  // Trade markers: entry triangle-up, exit triangle-down, placed on the
  // PRICE LINE (close at entry/exit bar) — NOT at vectorbt's
  // "Avg Entry Price" which is a weighted average of all partial fills
  // throughout the position life and drifts far from the actual price
  // at entry_time when the position is held while price moves.
  //
  // The trade's holding period is highlighted by colouring the segment
  // of the real price line green (winning) or red (losing) — replaces
  // the previous diagonal entry-to-exit connector which visually
  // suggested a fake monotonic equity path.
  if (symTrades.length > 0) {
    // Lookup: timestamp -> bar index (for slicing the OHLCV close array).
    const idxByTime = new Map<string, number>();
    for (let i = 0; i < ohlcv.timestamp.length; i++) {
      idxByTime.set(ohlcv.timestamp[i], i);
    }

    const entryX: string[] = [];
    const entryY: number[] = [];
    const entryColors: string[] = [];
    const entryHovers: string[] = [];

    const exitX: string[] = [];
    const exitY: number[] = [];
    const exitColors: string[] = [];
    const exitHovers: string[] = [];

    // Holding-period highlight: split into win/loss traces so colour
    // can vary, with `null` separators so disjoint trades don't connect.
    const winSegX: (string | null)[] = [];
    const winSegY: (number | null)[] = [];
    const lossSegX: (string | null)[] = [];
    const lossSegY: (number | null)[] = [];

    for (const t of symTrades) {
      const eIdx = idxByTime.get(t.entry_time);
      const xIdx = idxByTime.get(t.exit_time);
      const winning = t.pnl_quote > 0;
      const isLong = String(t.direction).toLowerCase().startsWith("long");
      const entryColor = isLong ? "#10b981" : "#a855f7";
      const exitColor = winning ? "#10b981" : "#ef4444";

      // Y-position of marker: real close at the bar (on the price line).
      // Fall back to vectorbt's avg-price if the bar isn't in the OHLCV
      // window (shouldn't happen normally).
      const eY = eIdx !== undefined ? ohlcv.close[eIdx] : t.entry_price;
      const xY = xIdx !== undefined ? ohlcv.close[xIdx] : t.exit_price;

      const pnlStr = `pnl ${(t.return_pct * 100).toFixed(2)}% ($${t.pnl_quote.toFixed(2)})`;
      const durStr = `${t.duration_hours.toFixed(1)}h`;
      const hover =
        `${t.direction} ${activeSymbol}<br>` +
        `entry ${t.entry_time}<br>close @ ${eY.toFixed(2)} ` +
        `(avg fill ${t.entry_price.toFixed(2)})<br>` +
        `exit ${t.exit_time}<br>close @ ${xY.toFixed(2)} ` +
        `(avg fill ${t.exit_price.toFixed(2)})<br>` +
        `${pnlStr} • ${durStr}<extra></extra>`;

      entryX.push(t.entry_time);
      entryY.push(eY);
      entryColors.push(entryColor);
      entryHovers.push(hover);

      exitX.push(t.exit_time);
      exitY.push(xY);
      exitColors.push(exitColor);
      exitHovers.push(hover);

      // Holding-period highlight: copy actual close prices for every bar
      // in [eIdx, xIdx]. Only emit if both ends are in the OHLCV window.
      if (eIdx !== undefined && xIdx !== undefined && xIdx >= eIdx) {
        const arrX = winning ? winSegX : lossSegX;
        const arrY = winning ? winSegY : lossSegY;
        for (let i = eIdx; i <= xIdx; i++) {
          arrX.push(ohlcv.timestamp[i]);
          arrY.push(ohlcv.close[i]);
        }
        arrX.push(null);
        arrY.push(null);
      }
    }

    if (winSegX.length > 0) {
      traces.push({
        x: winSegX, y: winSegY,
        mode: "lines", type: "scatter", name: "winning trades",
        line: { color: "#10b981", width: 2.5 },
        hoverinfo: "skip",
        showlegend: true,
      });
    }
    if (lossSegX.length > 0) {
      traces.push({
        x: lossSegX, y: lossSegY,
        mode: "lines", type: "scatter", name: "losing trades",
        line: { color: "#ef4444", width: 2.5 },
        hoverinfo: "skip",
        showlegend: true,
      });
    }

    traces.push({
      x: entryX, y: entryY,
      mode: "markers", type: "scatter", name: "entries",
      marker: {
        symbol: "triangle-up", size: 11,
        color: entryColors, line: { color: "#0f172a", width: 1 },
      },
      hovertemplate: entryHovers,
      hoverinfo: "text",
    });
    traces.push({
      x: exitX, y: exitY,
      mode: "markers", type: "scatter", name: "exits",
      marker: {
        symbol: "triangle-down", size: 11,
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
