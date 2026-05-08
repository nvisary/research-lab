import { useEffect, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import { api, type TradesPayload, type TradeRow } from "../api";
import { fmt, fmtPct } from "../format";
import { helpFor } from "../metricsHelp";
import { Tooltip } from "./Tooltip";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type Props = { strategy: string; iter: number | null };

type Tab = "summary" | "winners" | "losers" | "all";

export function TradesCard({ strategy, iter }: Props) {
  const [data, setData] = useState<TradesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("summary");

  useEffect(() => {
    if (iter === null) {
      setData(null);
      return;
    }
    setError(null);
    setData(null);
    api.trades(strategy, iter)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [strategy, iter]);

  if (iter === null) return <em className="text-slate-500">no iteration selected</em>;
  if (error) return <span className="text-rose-400">{error}</span>;
  if (!data) return <span className="text-slate-500">loading…</span>;

  const s = data.summary;
  const histTrace = {
    x: data.rows.map((r) => r.return_pct * 100),
    type: "histogram" as const,
    nbinsx: 60,
    marker: { color: "#60a5fa" },
    name: "per-trade return %",
  };

  return (
    <div>
      <div className="flex gap-3 mb-3 text-sm">
        {(["summary", "winners", "losers", "all"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1 rounded ${tab === t
              ? "bg-blue-500 text-white font-medium"
              : "bg-slate-800 text-slate-300 hover:bg-slate-700"}`}
          >
            {t}
          </button>
        ))}
        <span className="text-slate-500 ml-auto self-center">
          {data.row_count_total} trades total
          {data.row_count_total > 1000 && " (showing first 1000)"}
        </span>
      </div>

      {tab === "summary" && (
        <div className="grid md:grid-cols-2 gap-6">
          <table className="text-sm">
            <tbody>
              <KV k="trades" v={s.n_trades} />
              <KV k="win rate" v={fmtPct(s.win_rate)} />
              <KV k="wins / losses" v={`${s.n_wins ?? 0} / ${s.n_losses ?? 0}`} />
              <KV k="avg win" v={`$${fmt(s.avg_win, 2)}`} />
              <KV k="avg loss" v={`$${fmt(s.avg_loss, 2)}`} />
              <KV k="payoff ratio" v={fmt(s.payoff_ratio, 2)} />
              <KV k="total PnL" v={`$${fmt(s.total_pnl, 2)}`} />
              <KV k="median duration" v={`${fmt(s.median_duration_hours, 1)}h`} />
            </tbody>
          </table>
          <Plot
            data={[histTrace]}
            style={{ width: "100%", height: 240 }}
            useResizeHandler
            config={{ displaylogo: false, responsive: true }}
            layout={{
              paper_bgcolor: "rgba(0,0,0,0)",
              plot_bgcolor: "rgba(0,0,0,0)",
              font: { color: "#cbd5e1", size: 11 },
              margin: { t: 10, b: 36, l: 50, r: 10 },
              xaxis: { title: { text: "per-trade return (%)" }, gridcolor: "#334155" },
              yaxis: { title: { text: "count" }, gridcolor: "#334155" },
              shapes: [
                { type: "line", x0: 0, x1: 0, yref: "paper", y0: 0, y1: 1,
                  line: { color: "#94a3b8", dash: "dash", width: 1 } },
              ],
            }}
          />
        </div>
      )}

      {tab === "winners" && <TradeTable rows={data.top_winners} />}
      {tab === "losers" && <TradeTable rows={data.top_losers} />}
      {tab === "all" && <TradeTable rows={data.rows} max={500} />}
    </div>
  );
}

function TradeTable({ rows, max }: { rows: TradeRow[]; max?: number }) {
  const limited = max ? rows.slice(0, max) : rows;
  if (limited.length === 0) return <em className="text-slate-500">no trades</em>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-slate-400 uppercase tracking-wider">
          <tr>
            <Th>entry</Th>
            <Th>exit</Th>
            <Th>symbol</Th>
            <Th>side</Th>
            <Th>entry $</Th>
            <Th>exit $</Th>
            <Th>PnL $</Th>
            <Th>return</Th>
            <Th>duration</Th>
            <Th>w</Th>
            <Th>slice</Th>
          </tr>
        </thead>
        <tbody>
          {limited.map((r, i) => (
            <tr key={i} className="border-t border-edge">
              <Td>{new Date(r.entry_time).toISOString().slice(0, 16).replace("T", " ")}</Td>
              <Td>{new Date(r.exit_time).toISOString().slice(0, 16).replace("T", " ")}</Td>
              <Td>{r.symbol}</Td>
              <Td className={r.direction === "Long" ? "text-emerald-400" : "text-rose-400"}>{r.direction}</Td>
              <Td className="mono">{fmt(r.entry_price, 2)}</Td>
              <Td className="mono">{fmt(r.exit_price, 2)}</Td>
              <Td className={`mono ${r.pnl_quote >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {fmt(r.pnl_quote, 2)}
              </Td>
              <Td className={`mono ${r.return_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {fmtPct(r.return_pct, 2)}
              </Td>
              <Td className="mono">{fmt(r.duration_hours, 1)}h</Td>
              <Td>{r.window}</Td>
              <Td className="text-slate-400">{r.slice}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  const help = helpFor(k);
  return (
    <tr>
      <th className="text-left text-slate-400 font-medium pr-4 py-1 align-top w-32">
        {help ? <Tooltip text={help}>{k}</Tooltip> : k}
      </th>
      <td className="py-1 mono">{v}</td>
    </tr>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  const text = typeof children === "string" ? helpFor(children) : undefined;
  return (
    <th className="text-left px-2 py-1.5 font-medium">
      {text ? <Tooltip text={text}>{children}</Tooltip> : children}
    </th>
  );
}

function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-2 py-1 ${className}`}>{children}</td>;
}
