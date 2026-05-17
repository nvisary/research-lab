/**
 * CpcvCard — Combinatorial Purged CV runner + visualization.
 *
 * Lets the operator:
 *   - Kick off a CPCV run (n_groups × k_test → C(n,k) test paths) from the UI.
 *   - See the list of past runs (each tagged with iter, n_paths, overfit verdict).
 *   - For the most recent report: see the IS-vs-OOS Sharpe scatter (one
 *     dot per CPCV path), summary distribution, and the overfit verdict.
 *
 * The IS-vs-OOS scatter is the key generalisation check. A healthy
 * strategy shows positive correlation: paths that look good IS also
 * look good OOS. A scatter centered around the diagonal with slope
 * ≈ 1 is the gold standard. Slope < 0 = fitted to IS noise.
 */
import { useEffect, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import { api, type CpcvLatestPayload, type CpcvListEntry, type Job } from "../api";
import { fmt, fmtPct } from "../format";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

type Props = { name: string };

type CpcvForm = {
  start: string;
  end: string;
  tf: string;
  n_groups: number;
  k_test: number;
  embargo: string;
  cost_model: "static" | "spread" | "full";
};

const defaultForm: CpcvForm = {
  start: "2024-01-01",
  end: "2026-01-01",
  tf: "",
  n_groups: 10,
  k_test: 2,
  embargo: "1D",
  cost_model: "static",
};

export function CpcvCard({ name }: Props) {
  const [latest, setLatest] = useState<CpcvLatestPayload>(null);
  const [list, setList] = useState<CpcvListEntry[]>([]);
  const [form, setForm] = useState<CpcvForm>(defaultForm);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    try {
      const [l, ls] = await Promise.all([
        api.cpcvLatest(name).catch(() => null),
        api.cpcvList(name).catch(() => []),
      ]);
      setLatest(l);
      setList(ls);
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [name]);

  // Poll job until done, then refresh.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        const j = await api.job(job.id);
        setJob(j);
        if (j.status === "done") reload();
        if (j.status === "done" || j.status === "failed") clearInterval(t);
      } catch {/* keep polling */}
    }, 1500);
    return () => clearInterval(t);
  /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [job?.id, job?.status]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const j = await api.cpcvRun(name, {
        start: form.start, end: form.end,
        tf: form.tf || null,
        n_groups: form.n_groups, k_test: form.k_test,
        embargo: form.embargo || null,
        cost_model: form.cost_model,
      });
      setJob(j);
    } catch (e) {
      setError(String(e));
    }
  };

  const r = latest?.report;
  const paths = latest?.paths ?? [];
  const overfit = r?.overfit;
  const verdict = r?.overfit_verdict;

  // Build scatter trace: IS Sharpe x, OOS Sharpe y.
  const scatterTrace = paths.length > 0 ? [{
    x: paths.map((p) => p.is_sharpe),
    y: paths.map((p) => p.sharpe),
    mode: "markers" as const,
    type: "scatter" as const,
    marker: {
      color: paths.map((p) => p.sharpe),
      colorscale: "RdYlGn" as any,
      cmin: -1, cmax: 2, size: 8,
      showscale: false,
      line: { color: "#0f172a", width: 0.5 },
    },
    text: paths.map((p) =>
      `groups=[${p.test_groups}]<br>n_trades=${p.n_trades}<br>` +
      `MaxDD=${(p.max_dd * 100).toFixed(1)}%<br>TotR=${(p.total_return * 100).toFixed(1)}%`
    ),
    hovertemplate: "IS Sh %{x:.2f}<br>OOS Sh %{y:.2f}<br>%{text}<extra></extra>",
    name: "CPCV path",
  }] : [];

  // y=x and OLS regression overlays.
  const overlays: any[] = [];
  if (paths.length >= 2) {
    const xs = paths.map((p) => p.is_sharpe);
    const ys = paths.map((p) => p.sharpe);
    const mn = Math.min(...xs, ...ys);
    const mx = Math.max(...xs, ...ys);
    overlays.push({
      x: [mn, mx], y: [mn, mx],
      mode: "lines" as const, type: "scatter" as const,
      line: { color: "#475569", dash: "dot", width: 1 },
      name: "y = x", showlegend: true,
    });
    if (overfit?.slope_oos_on_is !== null && overfit?.slope_oos_on_is !== undefined &&
        overfit?.intercept_oos !== null && overfit?.intercept_oos !== undefined) {
      const b = overfit.slope_oos_on_is;
      const a = overfit.intercept_oos;
      overlays.push({
        x: [mn, mx], y: [a + b * mn, a + b * mx],
        mode: "lines" as const, type: "scatter" as const,
        line: { color: "#f59e0b", width: 2 },
        name: `OLS slope ${b.toFixed(2)}`,
      });
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3 text-sm">
        <Field label="start">
          <input type="date" value={form.start}
            onChange={(e) => setForm({ ...form, start: e.target.value })}
            className="input" />
        </Field>
        <Field label="end">
          <input type="date" value={form.end}
            onChange={(e) => setForm({ ...form, end: e.target.value })}
            className="input" />
        </Field>
        <Field label="tf (blank=strategy)">
          <input value={form.tf}
            onChange={(e) => setForm({ ...form, tf: e.target.value })}
            className="input w-20" placeholder="1h" />
        </Field>
        <Field label="n_groups">
          <input type="number" min={4} max={20} value={form.n_groups}
            onChange={(e) => setForm({ ...form, n_groups: +e.target.value })}
            className="input w-16" />
        </Field>
        <Field label="k_test">
          <input type="number" min={1} max={form.n_groups - 1} value={form.k_test}
            onChange={(e) => setForm({ ...form, k_test: +e.target.value })}
            className="input w-16" />
        </Field>
        <Field label="embargo">
          <input value={form.embargo}
            onChange={(e) => setForm({ ...form, embargo: e.target.value })}
            className="input w-16" placeholder="1D" />
        </Field>
        <Field label="cost model">
          <select value={form.cost_model}
            onChange={(e) => setForm({ ...form, cost_model: e.target.value as any })}
            className="input">
            <option value="static">static</option>
            <option value="spread">spread</option>
            <option value="full">full</option>
          </select>
        </Field>
        <button
          type="submit"
          disabled={job?.status === "running"}
          className="bg-blue-500 hover:bg-blue-400 disabled:bg-slate-600 px-4 py-1.5 rounded font-semibold">
          {job?.status === "running" ? "running…" : "run CPCV"}
        </button>
        <span className="text-xs text-slate-500">
          n_paths = C({form.n_groups}, {form.k_test}) = {combinations(form.n_groups, form.k_test)}
        </span>
      </form>
      {error && <div className="text-rose-400 text-sm">{error}</div>}
      {job && (
        <pre className="bg-slate-950 text-slate-300 text-xs p-3 rounded max-h-40 overflow-auto whitespace-pre-wrap">
          {`[${job.status}${job.exit_code !== null ? ` exit=${job.exit_code}` : ""}] ${job.cmd.join(" ")}\n\n${job.tail.slice(-15).join("\n")}`}
        </pre>
      )}

      {/* Verdict + summary tiles */}
      {r ? (
        <>
          <div className="flex flex-wrap items-center gap-4">
            <div className={`px-3 py-1 rounded text-sm ${verdictTone(verdict)}`}>
              {verdict ?? "no verdict"}
            </div>
            <div className="text-xs text-slate-400">
              {r.n_paths} paths · n_groups={r.n_groups} · k_test={r.k_test} ·
              embargo={r.embargo} · iter={r.iter} · ran {new Date(r.ran_at).toLocaleString()}
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            <Tile label="median Sharpe (OOS)" value={fmt(r.summary.median_sharpe, 3)} />
            <Tile label="IQR Sharpe" value={
              r.summary.iqr_sharpe ? `${fmt(r.summary.iqr_sharpe[0], 2)} … ${fmt(r.summary.iqr_sharpe[1], 2)}` : "—"
            } />
            <Tile label="% paths Sh > 0" value={fmt(r.summary.pct_positive_sharpe, 1)} sub="%" />
            <Tile label="% paths Sh > 1" value={fmt(r.summary.pct_above_1, 1)} sub="%" />
            <Tile label="worst MaxDD" value={fmtPct(r.summary.worst_max_dd, 1)} />
          </div>

          {paths.length > 0 && (
            <div>
              <h3 className="text-xs uppercase tracking-wider text-slate-400 mb-1">
                IS vs OOS Sharpe per path
                {overfit?.spearman_is_oos !== null && overfit?.spearman_is_oos !== undefined && (
                  <span className="ml-2 font-normal normal-case text-slate-300">
                    Spearman ρ = {fmt(overfit.spearman_is_oos, 3)}
                  </span>
                )}
                {overfit?.logit_overfit !== null && overfit?.logit_overfit !== undefined && (
                  <span className={`ml-2 font-normal normal-case ${(overfit.logit_overfit ?? 0) < 0 ? "text-emerald-400" : (overfit.logit_overfit ?? 0) >= 1 ? "text-rose-400" : "text-amber-400"}`}>
                    logit_overfit = {fmt(overfit.logit_overfit, 3)}
                  </span>
                )}
              </h3>
              <Plot
                data={[...overlays, ...scatterTrace]}
                layout={{
                  autosize: true, height: 360,
                  margin: { l: 50, r: 10, t: 10, b: 40 },
                  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
                  xaxis: { title: "IS Sharpe", gridcolor: "#334155", color: "#94a3b8", zeroline: true, zerolinecolor: "#475569" },
                  yaxis: { title: "OOS Sharpe", gridcolor: "#334155", color: "#94a3b8", zeroline: true, zerolinecolor: "#475569" },
                  legend: { font: { color: "#cbd5e1", size: 10 } },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: 360 }}
              />
            </div>
          )}
        </>
      ) : (
        <em className="text-slate-500">no CPCV runs yet — submit the form above.</em>
      )}

      {list.length > 1 && (
        <details>
          <summary className="text-xs uppercase tracking-wider text-slate-400 cursor-pointer">
            past CPCV runs ({list.length})
          </summary>
          <table className="w-full text-xs mt-2">
            <thead className="text-slate-400">
              <tr>
                <th className="text-left py-1 pr-3">ran_at</th>
                <th className="text-left py-1 pr-3">iter</th>
                <th className="text-left py-1 pr-3">paths</th>
                <th className="text-left py-1 pr-3">n_groups / k_test</th>
                <th className="text-left py-1 pr-3">median Sh</th>
                <th className="text-left py-1 pr-3">ρ IS↔OOS</th>
                <th className="text-left py-1">verdict</th>
              </tr>
            </thead>
            <tbody>
              {list.map((e) => (
                <tr key={e.file} className="border-t border-edge">
                  <td className="py-1 pr-3 text-slate-500">{new Date(e.ran_at).toLocaleString()}</td>
                  <td className="py-1 pr-3 mono">{e.iter}</td>
                  <td className="py-1 pr-3 mono">{e.n_paths}</td>
                  <td className="py-1 pr-3 mono">{e.n_groups} / {e.k_test}</td>
                  <td className="py-1 pr-3 mono">{fmt(e.median_sharpe, 3)}</td>
                  <td className="py-1 pr-3 mono">{fmt(e.spearman_is_oos, 3)}</td>
                  <td className={`py-1 ${verdictTone(e.overfit_verdict)} px-2 rounded inline-block`}>
                    {e.overfit_verdict ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border border-edge rounded p-2 bg-slate-900/50">
      <div className="text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className="mono text-lg text-slate-100">{value}{sub ? <span className="text-slate-500 text-xs ml-1">{sub}</span> : null}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col text-xs uppercase tracking-wider text-slate-400">
      {label}
      <div className="mt-1">{children}</div>
    </label>
  );
}

function verdictTone(v: string | null | undefined): string {
  if (!v) return "bg-slate-500/10 text-slate-400";
  if (v.startsWith("✓")) return "bg-emerald-500/10 text-emerald-400";
  if (v.startsWith("⚠")) return "bg-amber-500/10 text-amber-400";
  if (v.startsWith("✗")) return "bg-rose-500/10 text-rose-400";
  return "bg-slate-500/10 text-slate-400";
}

function combinations(n: number, k: number): number {
  if (k < 0 || k > n) return 0;
  k = Math.min(k, n - k);
  let num = 1, den = 1;
  for (let i = 1; i <= k; i++) { num *= n - k + i; den *= i; }
  return Math.round(num / den);
}

export default CpcvCard;
