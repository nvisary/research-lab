/**
 * Portfolio page — operator-driven multi-strategy composition.
 *
 * Pick N strategies, allocate $ capital to each, hit Run. Backend runs
 * each independently (Path B), sums capital-scaled equity into one
 * combined curve, returns metrics + correlation matrix.
 */
import { useEffect, useMemo, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import { api, type PortfolioReport, type PortfolioStrategyMeta } from "../api";
import { fmt, fmtPct } from "../format";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

const COLORS = ["#60a5fa", "#34d399", "#f59e0b", "#a78bfa", "#f472b6", "#22d3ee"];

type Row = { id: number; strategy: string; capital: number };

export function Portfolio() {
  const [available, setAvailable] = useState<PortfolioStrategyMeta[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [start, setStart] = useState("2024-01-01");
  const [end, setEnd] = useState("2026-01-01");
  const [costModel, setCostModel] = useState("static");
  const [report, setReport] = useState<PortfolioReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.portfolioStrategies()
      .then((s) => {
        setAvailable(s);
        // Auto-populate with all strategies, even allocations
        if (s.length > 0 && rows.length === 0) {
          const each = Math.round(10000 / s.length);
          setRows(s.map((m, i) => ({
            id: i, strategy: m.name, capital: each,
          })));
        }
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalCapital = rows.reduce((sum, r) => sum + (r.capital || 0), 0);

  function addRow() {
    const used = new Set(rows.map((r) => r.strategy));
    const next = available.find((m) => !used.has(m.name));
    if (!next) return;
    setRows([...rows, { id: Date.now(), strategy: next.name, capital: 1000 }]);
  }
  function removeRow(id: number) {
    setRows(rows.filter((r) => r.id !== id));
  }
  function updateRow(id: number, patch: Partial<Row>) {
    setRows(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  async function runPortfolio() {
    if (rows.length === 0) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const r = await api.portfolioRun({
        components: rows.map((r) => ({
          strategy: r.strategy,
          capital: r.capital,
        })),
        start, end,
        cost_model: costModel,
      });
      setReport(r);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  if (error && !report) return (
    <div className="text-rose-400 mb-4">Error: {error}</div>
  );

  return (
    <div className="space-y-4">
      <div className="bg-panel border border-edge rounded-lg p-4">
        <h2 className="font-semibold mb-3">Portfolio composition</h2>

        <div className="space-y-2 mb-3">
          {rows.map((r, i) => {
            const meta = available.find((m) => m.name === r.strategy);
            const pct = totalCapital > 0 ? (r.capital / totalCapital) * 100 : 0;
            return (
              <div key={r.id} className="flex items-center gap-3 text-sm">
                <span className="w-3 h-3 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
                <select
                  className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100 min-w-[180px]"
                  value={r.strategy}
                  onChange={(e) => updateRow(r.id, { strategy: e.target.value })}
                >
                  {available.map((m) => (
                    <option key={m.name} value={m.name}>{m.name}</option>
                  ))}
                </select>
                <span className="text-slate-500 text-xs">
                  {meta?.tf ?? "—"} · {meta?.symbols?.length ?? 0} sym
                </span>
                <span className="text-slate-500 ml-2">$</span>
                <input
                  type="number"
                  className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100 w-28"
                  value={r.capital}
                  onChange={(e) => updateRow(r.id, { capital: Number(e.target.value) })}
                  step={500}
                  min={0}
                />
                <span className="text-slate-500 text-xs">{pct.toFixed(1)}%</span>
                <button
                  onClick={() => removeRow(r.id)}
                  className="ml-auto text-rose-400 hover:text-rose-300 text-xs"
                  title="remove"
                >
                  ✕
                </button>
              </div>
            );
          })}
          <button
            onClick={addRow}
            disabled={rows.length >= available.length}
            className="text-blue-400 hover:text-blue-300 text-xs disabled:text-slate-600"
          >
            + add strategy
          </button>
        </div>

        <div className="flex items-end gap-3 flex-wrap pt-3 border-t border-edge">
          <label className="text-xs text-slate-400">
            start
            <input
              type="text"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="block mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100 w-32"
            />
          </label>
          <label className="text-xs text-slate-400">
            end
            <input
              type="text"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="block mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100 w-32"
            />
          </label>
          <label className="text-xs text-slate-400">
            cost model
            <select
              value={costModel}
              onChange={(e) => setCostModel(e.target.value)}
              className="block mt-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100"
            >
              <option value="static">static</option>
              <option value="spread">spread</option>
              <option value="full">full</option>
            </select>
          </label>
          <span className="text-sm text-slate-300 ml-auto">
            Total: <strong>${totalCapital.toLocaleString()}</strong>
          </span>
          <button
            onClick={runPortfolio}
            disabled={loading || rows.length === 0}
            className="bg-blue-500 hover:bg-blue-400 disabled:bg-slate-700 disabled:text-slate-500
                       text-white font-medium px-4 py-2 rounded text-sm"
          >
            {loading ? "running…" : "Run portfolio"}
          </button>
        </div>
        {error && (
          <div className="mt-3 text-rose-400 text-xs">{error}</div>
        )}
      </div>

      {report && <PortfolioReportView report={report} />}
    </div>
  );
}


function PortfolioReportView({ report }: { report: PortfolioReport }) {
  const traces = useMemo(() => {
    const out: any[] = [
      {
        x: report.combined_curve.timestamp,
        y: report.combined_curve.equity,
        mode: "lines", type: "scatter", name: "portfolio (total)",
        line: { color: "#f8fafc", width: 2.5 },
      },
      {
        x: report.combined_curve.timestamp,
        y: report.combined_curve.benchmark,
        mode: "lines", type: "scatter", name: "buy & hold (combined)",
        line: { color: "#94a3b8", dash: "dot", width: 1 },
      },
    ];
    Object.entries(report.per_strategy_curves).forEach(([name, c], i) => {
      out.push({
        x: c.timestamp, y: c.equity,
        mode: "lines", type: "scatter", name,
        line: { color: COLORS[i % COLORS.length], width: 1.2 },
      });
    });
    return out;
  }, [report]);

  const corrTable = useMemo(() => {
    const names = Object.keys(report.correlation_matrix);
    return { names, matrix: report.correlation_matrix };
  }, [report]);

  return (
    <>
      <div className="bg-panel border border-edge rounded-lg p-4">
        <h2 className="font-semibold mb-3">Portfolio equity</h2>
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
            yaxis: { title: { text: "equity ($)" }, gridcolor: "#334155" },
            legend: { orientation: "h", y: -0.2 },
          }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-panel border border-edge rounded-lg p-4">
          <h2 className="font-semibold mb-3">Portfolio metrics</h2>
          <table className="text-sm w-full">
            <tbody>
              <KV k="Total capital" v={`$${report.total_capital.toLocaleString()}`} />
              <KV k="Final equity"   v={`$${report.final_equity.toLocaleString(undefined, {maximumFractionDigits: 0})}`} />
              <KV k="Total PnL"      v={
                <span className={report.total_pnl_pct > 0 ? "text-emerald-400" : "text-rose-400"}>
                  ${report.total_pnl_dollar.toFixed(2)} ({fmtPct(report.total_pnl_pct)})
                </span>
              } />
              <KV k="Sharpe"         v={fmt(report.portfolio_metrics?.sharpe, 3)} />
              <KV k="Sortino"        v={fmt(report.portfolio_metrics?.sortino, 3)} />
              <KV k="Max DD"         v={fmtPct(report.portfolio_metrics?.max_dd)} />
              <KV k="CAGR"           v={fmtPct(report.portfolio_metrics?.cagr)} />
              <KV k="Alpha vs b&h"   v={
                <span className={
                  (report.portfolio_metrics?.alpha_sharpe ?? 0) > 0.1 ? "text-emerald-400"
                  : (report.portfolio_metrics?.alpha_sharpe ?? 0) < -0.1 ? "text-rose-400"
                  : "text-amber-400"
                }>
                  {fmt(report.portfolio_metrics?.alpha_sharpe, 3)}
                </span>
              } />
              <KV k="bench Sharpe"   v={fmt(report.portfolio_metrics?.bench_sharpe, 3)} />
              <KV k="hit rate"       v={fmtPct(report.portfolio_metrics?.hit_rate)} />
              <KV k="period"         v={`${report.period[0]} → ${report.period[1]}`} />
            </tbody>
          </table>
        </div>

        <div className="bg-panel border border-edge rounded-lg p-4">
          <h2 className="font-semibold mb-3">Correlation matrix</h2>
          <div className="text-xs text-slate-400 mb-2">
            Daily-return correlation. Lower = better diversification. Pairs &lt; 0.3 are
            structurally complementary.
          </div>
          {corrTable.names.length === 0 ? (
            <em className="text-slate-500">no data</em>
          ) : (
            <table className="text-xs">
              <thead>
                <tr>
                  <th></th>
                  {corrTable.names.map((n) => (
                    <th key={n} className="px-2 py-1 text-slate-400 font-normal">{n}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {corrTable.names.map((row) => (
                  <tr key={row}>
                    <th className="px-2 py-1 text-slate-400 font-normal text-right">{row}</th>
                    {corrTable.names.map((col) => {
                      const v = corrTable.matrix[row]?.[col];
                      const t = v === null ? 0 : Math.abs(v ?? 0);
                      const bg = row === col ? "rgb(30, 41, 59)"
                        : t > 0.7 ? "rgba(239, 68, 68, 0.5)"
                        : t > 0.3 ? "rgba(245, 158, 11, 0.4)"
                        : "rgba(34, 197, 94, 0.3)";
                      return (
                        <td key={col} className="px-2 py-1 text-center text-slate-100"
                            style={{ background: bg, minWidth: 60 }}>
                          {v === null ? "—" : v.toFixed(3)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="bg-panel border border-edge rounded-lg p-4">
        <h2 className="font-semibold mb-3">Per-strategy contribution</h2>
        <table className="text-sm w-full">
          <thead className="text-slate-400">
            <tr>
              <th className="text-left py-1">strategy</th>
              <th className="text-right">capital</th>
              <th className="text-right">final eq</th>
              <th className="text-right">PnL ($)</th>
              <th className="text-right">PnL (%)</th>
              <th className="text-right">Sharpe</th>
              <th className="text-right">Max DD</th>
              <th className="text-right">trades</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(report.per_strategy).map(([name, ps]) => (
              <tr key={name} className="border-t border-edge">
                <td className="py-1">{name}</td>
                <td className="text-right">${ps.capital.toLocaleString()}</td>
                <td className="text-right">${ps.final_equity.toFixed(0)}</td>
                <td className={`text-right ${ps.pnl_dollar > 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  ${ps.pnl_dollar.toFixed(2)}
                </td>
                <td className={`text-right ${ps.pnl_pct > 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {fmtPct(ps.pnl_pct)}
                </td>
                <td className="text-right">{fmt(ps.metrics?.sharpe, 2)}</td>
                <td className="text-right">{fmtPct(ps.metrics?.max_dd)}</td>
                <td className="text-right">{ps.metrics?.n_trades ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function KV({ k, v }: { k: string; v: any }) {
  return (
    <tr>
      <th className="text-left text-slate-400 font-normal pr-4 py-1">{k}</th>
      <td className="py-1">{v}</td>
    </tr>
  );
}
