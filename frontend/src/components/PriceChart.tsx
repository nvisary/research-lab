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

  // Viewport-aware fetch: viewRange tracks the currently visible x-range.
  // On zoom/pan (Plotly relayout), we update viewRange; a debounced effect
  // re-fetches OHLCV for that range. Backend auto-coarsens the tf so we
  // get full resolution when zoomed in tight, and downsampled bars on
  // wide views. The strategy's native tf is the FINEST we ever request.
  const [viewRange, setViewRange] = useState<{ s: string; e: string }>({
    s: start, e: end,
  });
  const [debouncedRange, setDebouncedRange] =
    useState<{ s: string; e: string }>({ s: start, e: end });

  // Reset viewport when the strategy/iter window or symbol changes.
  useEffect(() => {
    setViewRange({ s: start, e: end });
    setDebouncedRange({ s: start, e: end });
  }, [start, end, activeSymbol]);

  // Debounce viewRange -> debouncedRange (300ms) so rapid pan/zoom
  // doesn't fire a request per frame.
  useEffect(() => {
    const h = setTimeout(() => setDebouncedRange(viewRange), 300);
    return () => clearTimeout(h);
  }, [viewRange]);

  useEffect(() => {
    if (!activeSymbol) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .ohlcv(activeSymbol, debouncedRange.s, debouncedRange.e, tf)
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
  }, [activeSymbol, debouncedRange.s, debouncedRange.e, tf]);

  const handleRelayout = (e: any) => {
    // Plotly emits one of two shapes on zoom/pan:
    //   { 'xaxis.range[0]': '2024-03-01 ...', 'xaxis.range[1]': '...' }
    //   { 'xaxis.range': ['2024-03-01 ...', '...'] }
    // Auto-range (double-click reset) emits { 'xaxis.autorange': true }.
    if (e["xaxis.autorange"]) {
      setViewRange({ s: start, e: end });
      return;
    }
    const r0 = e["xaxis.range[0]"] ?? e["xaxis.range"]?.[0];
    const r1 = e["xaxis.range[1]"] ?? e["xaxis.range"]?.[1];
    if (r0 != null && r1 != null) {
      // Clamp to the strategy's full window — we don't fetch beyond it.
      const s = String(r0) < start ? start : String(r0);
      const en = String(r1) > end ? end : String(r1);
      setViewRange({ s, e: en });
    }
  };

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

  if (error) return <div className="text-rose-400">price load failed: {error}</div>;
  if (!ohlcv) {
    return <div className="text-slate-500 italic">loading price…</div>;
  }

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
    // Snap trade timestamps to the loaded bar grid. Exact match used to
    // be enough when the chart was always at the strategy's native tf,
    // but the backend now auto-coarsens on wide views (e.g. 1h trades
    // on a 4h-rendered chart) so trade timestamps no longer hit the
    // bar grid exactly. Binary-search the largest bar index whose
    // timestamp is <= the trade's timestamp — i.e. the bar that
    // CONTAINS the trade event. Returns -1 if the trade is before the
    // first loaded bar.
    const tsArr = ohlcv.timestamp;
    const findBar = (t: string): number => {
      let lo = 0, hi = tsArr.length - 1, ans = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (tsArr[mid] <= t) { ans = mid; lo = mid + 1; }
        else                 { hi = mid - 1; }
      }
      return ans;
    };

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
      const eRaw = findBar(t.entry_time);
      const xRaw = findBar(t.exit_time);
      const eIdx: number | undefined = eRaw >= 0 ? eRaw : undefined;
      const xIdx: number | undefined = xRaw >= 0 ? xRaw : undefined;
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
      <div className="flex justify-end items-center gap-2 mb-1 text-xs text-slate-500">
        <span>
          tf: <span className="text-slate-300">{ohlcv.tf}</span>
          {ohlcv.tf_requested && ohlcv.tf_requested !== ohlcv.tf && (
            <span className="text-slate-500"> (auto from {ohlcv.tf_requested})</span>
          )}
          {" · "}
          <span>{ohlcv.n_bars} bars</span>
        </span>
        {loading && <span className="text-slate-400 italic">loading…</span>}
      </div>
      <Plot
        data={traces}
        style={{ width: "100%", height: 380 }}
        useResizeHandler
        config={{ displaylogo: false, responsive: true, scrollZoom: true }}
        onRelayout={handleRelayout}
        layout={{
          paper_bgcolor: "rgba(0,0,0,0)",
          plot_bgcolor: "rgba(0,0,0,0)",
          font: { color: "#cbd5e1", size: 11 },
          margin: { t: 24, b: 40, l: 60, r: 10 },
          xaxis: { gridcolor: "#334155" },
          yaxis: { title: { text: "price" }, gridcolor: "#334155" },
          legend: { orientation: "h", y: -0.18 },
          hovermode: "closest",
          // uirevision keeps the user's pan/zoom across re-renders driven
          // by data-only updates (refetched OHLCV for the same view).
          // Tied to symbol/window so changing strategy/symbol resets it.
          uirevision: `${activeSymbol}|${start}|${end}`,
        }}
      />
    </div>
  );
}
