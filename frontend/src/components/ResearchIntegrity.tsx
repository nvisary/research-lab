/**
 * ResearchIntegrity — session-level "are these numbers real?" dashboard.
 *
 * Surfaces what the harness now computes per iter (block-bootstrap p-values,
 * Harvey-Liu haircut Sharpe, session IS↔OOS overfit stats, trial-Sharpe
 * distribution) so the operator can read the multiple-testing reality
 * of the running iter loop at a glance.
 *
 * Renders three blocks:
 *   1. KPI tiles — raw vs deflated Sharpe (DSR + BHY haircut), session
 *      overfit verdict, selection premium.
 *   2. Trial-Sharpe distribution histogram with expected-max-under-null
 *      reference line, plus per-iter trajectory of raw vs BHY-haircut
 *      Sharpe so the user sees how much each additional trial costs.
 *   3. Bootstrap null-distribution histogram for the *latest* iter (block
 *      bootstrap with the strategy's own volatility/serial structure but
 *      mean re-centered to zero — H0:no-edge), with the observed Sharpe
 *      overlaid and the one-sided p-value.
 *
 * Plotly is reused from the existing equity/drawdown charts; no new deps.
 */
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import type { ResearchStatsPayload } from "../api";
import { fmt, fmtPct } from "../format";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type Props = { data: ResearchStatsPayload | null };

function overfitColor(verdict: string | null | undefined): string {
  if (!verdict) return "text-slate-500";
  if (verdict.startsWith("✓")) return "text-emerald-400";
  if (verdict.startsWith("⚠")) return "text-amber-400";
  if (verdict.startsWith("✗")) return "text-rose-400";
  return "text-slate-400";
}

function pValueColor(p: number | null | undefined): string {
  if (p === null || p === undefined) return "text-slate-500";
  if (p < 0.01) return "text-emerald-400 font-semibold";
  if (p < 0.05) return "text-emerald-300";
  if (p < 0.1) return "text-amber-400";
  return "text-rose-400";
}

export function ResearchIntegrity({ data }: Props) {
  if (!data || !data.history_present) {
    return (
      <em className="text-slate-500">
        no history yet — run an iteration to populate research-integrity stats.
      </em>
    );
  }

  const ts = data.trial_sharpes;
  const so = data.session_overfit ?? null;
  const latest = data.latest?.research_stats ?? null;
  const perIter = data.per_iter ?? [];

  const bhy = latest?.haircut_sharpe?.bhy;
  const raw = latest?.haircut_sharpe?.raw;
  const block = latest?.bootstrap?.block;
  const perm = latest?.bootstrap?.permutation;

  // Build trial-Sharpe histogram trace (if we have data).
  const histTraces: any[] = [];
  if (ts?.hist_edges && ts.hist_counts) {
    const edges = ts.hist_edges;
    const centers = edges.slice(0, -1).map((e, i) => (e + edges[i + 1]) / 2);
    histTraces.push({
      x: centers,
      y: ts.hist_counts,
      type: "bar",
      marker: { color: "#3b82f6", opacity: 0.7 },
      name: "trial Sharpes",
      hovertemplate: "Sharpe %{x:.2f}<br>count %{y}<extra></extra>",
    });
  }

  // Bootstrap-null histogram for latest iter.
  const nullTraces: any[] = [];
  if (block?.null_sharpe?.hist_edges && block.null_sharpe.hist_counts) {
    const edges = block.null_sharpe.hist_edges;
    const centers = edges.slice(0, -1).map((e, i) => (e + edges[i + 1]) / 2);
    nullTraces.push({
      x: centers,
      y: block.null_sharpe.hist_counts,
      type: "bar",
      marker: { color: "#64748b", opacity: 0.7 },
      name: "null Sharpe (H0: no edge)",
      hovertemplate: "Sharpe %{x:.2f}<br>count %{y}<extra></extra>",
    });
  }

  // Raw vs BHY-haircut trajectory.
  const trajIters = perIter.map((p) => p.iter);
  const rawSh = perIter.map((p) => p.oos_sharpe);
  const bhySh = perIter.map((p) => p.bhy_sharpe);
  const pVals = perIter.map((p) => p.p_sharpe_block);

  return (
    <div className="space-y-4">
      {/* ---- KPI tiles ---- */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <Tile
          label="Raw best Sharpe"
          value={fmt(ts?.max, 3)}
          sub={`across ${ts?.n ?? 0} iters`}
          tone="text-slate-100"
        />
        <Tile
          label="E[max | H0]"
          value={fmt(ts?.expected_max_under_null, 3)}
          sub="expected max under no-edge"
          tone="text-slate-400"
        />
        <Tile
          label="Selection premium"
          value={fmt(ts?.selection_premium, 3)}
          sub="best − E[max|H0]"
          tone={
            (ts?.selection_premium ?? 0) > 0.3
              ? "text-emerald-400"
              : (ts?.selection_premium ?? 0) > 0
              ? "text-amber-400"
              : "text-rose-400"
          }
        />
        <Tile
          label="BHY-haircut Sharpe"
          value={fmt(bhy?.sharpe, 3)}
          sub={
            bhy
              ? `haircut ${fmtPct(bhy.haircut_pct, 1)} of raw ${fmt(raw?.sharpe, 2)}`
              : "—"
          }
          tone={
            (bhy?.sharpe ?? 0) > 0.5
              ? "text-emerald-400"
              : (bhy?.sharpe ?? 0) > 0
              ? "text-amber-400"
              : "text-rose-400"
          }
        />
        <Tile
          label="Session IS↔OOS ρ"
          value={fmt(so?.spearman_is_oos, 3)}
          sub={
            so?.logit_overfit !== null && so?.logit_overfit !== undefined
              ? `logit_overfit ${so.logit_overfit >= 0 ? "+" : ""}${so.logit_overfit.toFixed(2)}`
              : "—"
          }
          tone={
            (so?.spearman_is_oos ?? 0) > 0.3 && (so?.logit_overfit ?? 0) < 0
              ? "text-emerald-400"
              : (so?.spearman_is_oos ?? 0) < 0 || (so?.logit_overfit ?? 0) >= 1
              ? "text-rose-400"
              : "text-amber-400"
          }
        />
      </div>

      {/* ---- Per-iter p-value & haircut trajectory ---- */}
      {perIter.length >= 2 && (
        <div>
          <h3 className="text-xs uppercase tracking-wider text-slate-400 mb-1">
            Per-iter Sharpe: raw vs BHY-haircut, block-bootstrap p-value
          </h3>
          <Plot
            data={[
              {
                x: trajIters, y: rawSh,
                mode: "lines+markers", type: "scatter",
                name: "raw OOS Sharpe",
                marker: { color: "#3b82f6" }, line: { color: "#3b82f6" },
              },
              {
                x: trajIters, y: bhySh,
                mode: "lines+markers", type: "scatter",
                name: "BHY-haircut Sharpe",
                marker: { color: "#22c55e" }, line: { color: "#22c55e", dash: "dot" },
              },
              {
                x: trajIters, y: pVals,
                mode: "lines+markers", type: "scatter",
                name: "p-value (block, H0:no edge)",
                yaxis: "y2",
                marker: { color: "#f97316", size: 6 }, line: { color: "#f97316" },
              },
            ]}
            layout={{
              autosize: true, height: 280,
              margin: { l: 50, r: 50, t: 10, b: 40 },
              paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
              xaxis: { title: "iter", gridcolor: "#334155", color: "#94a3b8" },
              yaxis: { title: "Sharpe", gridcolor: "#334155", color: "#94a3b8" },
              yaxis2: {
                title: "p-value", overlaying: "y", side: "right",
                range: [0, 1], color: "#f97316", gridcolor: "rgba(0,0,0,0)",
              },
              legend: { font: { color: "#cbd5e1", size: 10 } },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%", height: 280 }}
          />
        </div>
      )}

      {/* ---- Two side-by-side histograms ---- */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <h3 className="text-xs uppercase tracking-wider text-slate-400 mb-1">
            Trial-Sharpe distribution
            {ts?.expected_max_under_null !== undefined && (
              <span className="ml-2 text-amber-400 font-normal normal-case">
                E[max|H0]={fmt(ts.expected_max_under_null, 2)}
              </span>
            )}
          </h3>
          <Plot
            data={histTraces}
            layout={{
              autosize: true, height: 260,
              margin: { l: 40, r: 10, t: 10, b: 40 },
              paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
              xaxis: { title: "OOS Sharpe (per iter)", gridcolor: "#334155", color: "#94a3b8" },
              yaxis: { title: "count", gridcolor: "#334155", color: "#94a3b8" },
              shapes: ts?.expected_max_under_null !== undefined
                ? [{
                    type: "line",
                    x0: ts.expected_max_under_null, x1: ts.expected_max_under_null,
                    y0: 0, y1: 1, yref: "paper",
                    line: { color: "#f59e0b", width: 2, dash: "dash" },
                  }]
                : [],
              showlegend: false,
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%", height: 260 }}
          />
        </div>

        <div>
          <h3 className="text-xs uppercase tracking-wider text-slate-400 mb-1">
            Latest iter ({data.latest?.iter ?? "—"}): null distribution
            {block && (
              <span className="ml-2 font-normal normal-case">
                <span className={pValueColor(block.p_values.sharpe)}>
                  p(Sharpe)={fmt(block.p_values.sharpe, 3)}
                </span>
                <span className="text-slate-500"> · </span>
                <span className={pValueColor(block.p_values.max_dd)}>
                  p(DD)={fmt(block.p_values.max_dd, 3)}
                </span>
              </span>
            )}
          </h3>
          <Plot
            data={nullTraces}
            layout={{
              autosize: true, height: 260,
              margin: { l: 40, r: 10, t: 10, b: 40 },
              paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
              xaxis: { title: "Sharpe (block-bootstrap null)", gridcolor: "#334155", color: "#94a3b8" },
              yaxis: { title: "count", gridcolor: "#334155", color: "#94a3b8" },
              shapes: block
                ? [{
                    type: "line",
                    x0: block.observed.sharpe, x1: block.observed.sharpe,
                    y0: 0, y1: 1, yref: "paper",
                    line: { color: "#22c55e", width: 2 },
                  }]
                : [],
              annotations: block
                ? [{
                    x: block.observed.sharpe, y: 1, yref: "paper",
                    text: `observed ${block.observed.sharpe.toFixed(2)}`,
                    showarrow: false, font: { color: "#22c55e", size: 10 },
                    xanchor: "left", yanchor: "top",
                  }]
                : [],
              showlegend: false,
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%", height: 260 }}
          />
        </div>
      </div>

      {/* ---- Verbose readouts ---- */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
        <div className="border border-edge rounded p-3 bg-slate-900/50">
          <div className="text-xs uppercase tracking-wider text-slate-400 mb-1">
            Haircut Sharpe (latest iter)
          </div>
          {bhy ? (
            <table className="w-full">
              <tbody>
                <tr><td className="text-slate-400 pr-2">raw</td>
                    <td className="mono text-right">{fmt(raw?.sharpe, 3)}</td>
                    <td className="mono text-right text-slate-500">p={fmt(raw?.p_value, 4)}</td></tr>
                <tr><td className="text-slate-400 pr-2">Bonferroni</td>
                    <td className="mono text-right">{fmt(latest?.haircut_sharpe?.bonferroni.sharpe, 3)}</td>
                    <td className="mono text-right text-slate-500">−{fmtPct(latest?.haircut_sharpe?.bonferroni.haircut_pct, 1)}</td></tr>
                <tr><td className="text-slate-400 pr-2">Holm</td>
                    <td className="mono text-right">{fmt(latest?.haircut_sharpe?.holm.sharpe, 3)}</td>
                    <td className="mono text-right text-slate-500">−{fmtPct(latest?.haircut_sharpe?.holm.haircut_pct, 1)}</td></tr>
                <tr><td className="text-slate-400 pr-2">BHY</td>
                    <td className="mono text-right">{fmt(bhy.sharpe, 3)}</td>
                    <td className="mono text-right text-slate-500">−{fmtPct(bhy.haircut_pct, 1)}</td></tr>
              </tbody>
            </table>
          ) : (
            <em className="text-slate-500">no research_stats on latest iter</em>
          )}
        </div>

        <div className="border border-edge rounded p-3 bg-slate-900/50">
          <div className="text-xs uppercase tracking-wider text-slate-400 mb-1">
            Session overfit (IS↔OOS across iters)
          </div>
          {so ? (
            <table className="w-full">
              <tbody>
                <tr><td className="text-slate-400 pr-2">n iters paired</td>
                    <td className="mono text-right">{so.n_iters}</td></tr>
                <tr><td className="text-slate-400 pr-2">Spearman ρ</td>
                    <td className={`mono text-right ${(so.spearman_is_oos ?? 0) > 0.3 ? "text-emerald-400" : (so.spearman_is_oos ?? 0) < 0 ? "text-rose-400" : "text-amber-400"}`}>
                      {fmt(so.spearman_is_oos, 3)}</td></tr>
                <tr><td className="text-slate-400 pr-2">OLS slope OOS~IS</td>
                    <td className="mono text-right">{fmt(so.slope_oos_on_is, 3)}</td></tr>
                <tr><td className="text-slate-400 pr-2">logit_overfit</td>
                    <td className={`mono text-right ${(so.logit_overfit ?? 0) < 0 ? "text-emerald-400" : (so.logit_overfit ?? 0) >= 1 ? "text-rose-400" : "text-amber-400"}`}>
                      {fmt(so.logit_overfit, 3)}</td></tr>
                <tr><td className="text-slate-400 pr-2">selection inflation</td>
                    <td className="mono text-right">{fmt(so.selection_inflation, 3)}</td></tr>
                <tr><td className="text-slate-400 pr-2">IS-best OOS gap vs median</td>
                    <td className={`mono text-right ${(so.best_is_oos_gap ?? 0) > 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt(so.best_is_oos_gap, 3)}</td></tr>
              </tbody>
            </table>
          ) : (
            <em className="text-slate-500">need ≥4 iters with both train and OOS Sharpe</em>
          )}
        </div>
      </div>

      {/* ---- Perm vs block sanity ---- */}
      {block && perm && (
        <div className="text-xs text-slate-500">
          <span className="mr-3">
            p(Sh) block={fmt(block.p_values.sharpe, 3)} vs permutation={fmt(perm.p_values.sharpe, 3)}
          </span>
          <span className="mr-3">
            block_size={block.block_size} bars, n_boot={block.n_boot}
          </span>
          <span className="text-slate-600">
            (block preserves serial correlation; permutation destroys it — large gap = strategy depends on chronology)
          </span>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, sub, tone }:
              { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="border border-edge rounded p-2 bg-slate-900/50">
      <div className="text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className={`mono text-lg ${tone ?? "text-slate-100"}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export default ResearchIntegrity;
// Silence unused-import warning on overfitColor (kept for future tile use).
void overfitColor;
