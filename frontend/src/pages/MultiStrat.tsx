/**
 * MultiStrat page — cross-strategy hypothesis tests.
 *
 * Answers two questions the rest of the UI does not:
 *   Q1. Is the BEST of N strategies actually better than zero, after
 *       accounting for "best of N" selection? (Reality Check / SPA)
 *   Q2. For each strategy: is its mean return significant under FWER
 *       control across the family? (Romano-Wolf adjusted p-values)
 *
 * Inputs: which strategies to include + bootstrap parameters.
 * Outputs: three tests, a per-strategy table, correlation matrix
 *          heatmap, and per-strategy OOS equity curves.
 */
import { useEffect, useMemo, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import {
  api,
  type Job,
  type MultiStratCandidate,
  type MultiStratListEntry,
  type MultiStratPayload,
} from "../api";
import { fmt, fmtPct } from "../format";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);

const COLORS = [
  "#60a5fa", "#34d399", "#f59e0b", "#a78bfa", "#f472b6",
  "#22d3ee", "#fb7185", "#a3e635", "#facc15", "#94a3b8",
];

function pClass(p: number | null | undefined) {
  if (p === null || p === undefined || Number.isNaN(p))
    return "text-slate-500";
  if (p < 0.01) return "text-emerald-400 font-semibold";
  if (p < 0.05) return "text-emerald-400";
  if (p < 0.10) return "text-amber-400";
  return "text-rose-400";
}

function VerdictPill({ p, label }: { p: number; label: string }) {
  const cls = p < 0.05
    ? "bg-emerald-500/15 text-emerald-300"
    : p < 0.10
      ? "bg-amber-500/15 text-amber-300"
      : "bg-rose-500/10 text-rose-400";
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${cls}`}>
      {label}: p = {fmt(p, 3)}
    </span>
  );
}

export function MultiStrat() {
  const [candidates, setCandidates] = useState<MultiStratCandidate[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [nBoot, setNBoot] = useState(1000);
  const [seed, setSeed] = useState<string>("");
  const [benchmark, setBenchmark] = useState("0");
  const [join, setJoin] = useState<"inner" | "outer">("inner");

  const [payload, setPayload] = useState<MultiStratPayload | null>(null);
  const [history, setHistory] = useState<MultiStratListEntry[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ---- Initial load ----
  useEffect(() => {
    api.multistratCandidates()
      .then((c) => {
        setCandidates(c);
        // Default: select all candidates that have equity on disk.
        const init: Record<string, boolean> = {};
        for (const x of c) init[x.name] = x.equity_present;
        setSelected(init);
      })
      .catch((e) => setError(String(e)));
    api.multistratLatest()
      .then((p) => setPayload(p))
      .catch(() => {});
    api.multistratList()
      .then((l) => setHistory(l))
      .catch(() => {});
  }, []);

  // ---- Poll job until finished ----
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        const j = await api.job(job.id);
        setJob(j);
        if (j.status === "done") {
          const p = await api.multistratLatest();
          setPayload(p);
          const l = await api.multistratList();
          setHistory(l);
        }
      } catch {/* swallow polling errors */}
    }, 1500);
    return () => clearInterval(t);
  }, [job]);

  const selectedNames = useMemo(
    () => Object.entries(selected).filter(([_, v]) => v).map(([k]) => k),
    [selected],
  );

  async function runTest() {
    setError(null);
    try {
      const j = await api.multistratRun({
        strategies: selectedNames.length ? selectedNames : null,
        n_boot: nBoot,
        seed: seed === "" ? null : Number(seed),
        benchmark: Number(benchmark),
        join,
      });
      setJob(j);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  return (
    <div className="space-y-4">
      <div className="bg-panel border border-edge rounded-lg p-4">
        <h2 className="font-semibold mb-1">
          Multi-strategy hypothesis tests
        </h2>
        <p className="text-xs text-slate-400 mb-3 max-w-3xl">
          Reality Check, SPA, and Romano-Wolf jointly test whether the
          best-of-N strategy effect survives the selection penalty across
          your stable. Run after you have ≥2 strategies with stable
          OOS curves. Uses stationary block bootstrap on the joint daily
          returns matrix — joint correlations are preserved under H0.
        </p>

        <Composer
          candidates={candidates}
          selected={selected}
          onChange={setSelected}
          nBoot={nBoot}
          onNBoot={setNBoot}
          seed={seed}
          onSeed={setSeed}
          benchmark={benchmark}
          onBenchmark={setBenchmark}
          join={join}
          onJoin={setJoin}
          onRun={runTest}
          running={job?.status === "running" || job?.status === "pending"}
          selectedCount={selectedNames.length}
        />
      </div>

      {error && (
        <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded p-3 text-sm">
          {error}
        </div>
      )}

      {job && <JobCard job={job} />}

      {payload && <Report payload={payload} />}

      {history.length > 0 && <HistoryCard rows={history} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function Composer({
  candidates, selected, onChange,
  nBoot, onNBoot, seed, onSeed, benchmark, onBenchmark, join, onJoin,
  onRun, running, selectedCount,
}: {
  candidates: MultiStratCandidate[];
  selected: Record<string, boolean>;
  onChange: (v: Record<string, boolean>) => void;
  nBoot: number; onNBoot: (n: number) => void;
  seed: string; onSeed: (s: string) => void;
  benchmark: string; onBenchmark: (s: string) => void;
  join: "inner" | "outer"; onJoin: (v: "inner" | "outer") => void;
  onRun: () => void; running: boolean; selectedCount: number;
}) {
  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-3">
        {candidates.map((c) => {
          const checked = !!selected[c.name];
          const disabled = !c.equity_present;
          return (
            <label
              key={c.name}
              className={`flex items-start gap-2 p-2 rounded border text-sm ${
                disabled
                  ? "border-edge bg-bg/40 text-slate-500 cursor-not-allowed"
                  : "border-edge hover:border-slate-500 cursor-pointer"
              }`}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={(e) =>
                  onChange({ ...selected, [c.name]: e.target.checked })
                }
                className="mt-1"
              />
              <div className="flex-1 min-w-0">
                <div className="font-mono truncate">{c.name}</div>
                <div className="text-xs text-slate-500">
                  {c.best_iter !== null
                    ? `iter ${c.best_iter} · comp ${fmt(c.composite, 3)}`
                    : "no best"}
                </div>
                {disabled && (
                  <div className="text-xs text-amber-500">no equity parquet</div>
                )}
              </div>
            </label>
          );
        })}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-3 text-sm">
        <Field label="n_boot">
          <input
            type="number"
            value={nBoot}
            onChange={(e) => onNBoot(parseInt(e.target.value || "0", 10))}
            className="bg-bg border border-edge rounded px-2 py-1 w-full"
          />
        </Field>
        <Field label="seed (blank = nondet.)">
          <input
            value={seed}
            onChange={(e) => onSeed(e.target.value)}
            placeholder="(none)"
            className="bg-bg border border-edge rounded px-2 py-1 w-full"
          />
        </Field>
        <Field label="benchmark (daily)">
          <input
            value={benchmark}
            onChange={(e) => onBenchmark(e.target.value)}
            className="bg-bg border border-edge rounded px-2 py-1 w-full"
          />
        </Field>
        <Field label="alignment">
          <select
            value={join}
            onChange={(e) => onJoin(e.target.value as "inner" | "outer")}
            className="bg-bg border border-edge rounded px-2 py-1 w-full"
          >
            <option value="inner">inner (strict)</option>
            <option value="outer">outer (union)</option>
          </select>
        </Field>
        <div className="flex items-end">
          <button
            disabled={running || selectedCount < 2}
            onClick={onRun}
            className="bg-emerald-500/20 hover:bg-emerald-500/30 disabled:opacity-50 disabled:cursor-not-allowed text-emerald-300 border border-emerald-500/40 rounded px-3 py-1.5 text-sm w-full"
          >
            {running ? "running…" : `run on ${selectedCount} strategies`}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      {children}
    </label>
  );
}

// --------------------------------------------------------------------------- //
function JobCard({ job }: { job: Job }) {
  return (
    <div className="bg-panel border border-edge rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-sm">job {job.id}</span>
        <span className="text-xs text-slate-400">{job.status}</span>
      </div>
      <pre className="bg-bg border border-edge rounded p-2 text-xs max-h-48 overflow-y-auto whitespace-pre-wrap">
        {job.tail.join("\n")}
      </pre>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function Report({ payload }: { payload: MultiStratPayload }) {
  const { report } = payload;
  const t = report.tests;
  const rc = t.reality_check;
  const spa = t.spa;
  const rw = t.romano_wolf;

  // Sort per-strategy table by RW rank.
  const rows = [...t.per_strategy].sort(
    (a, b) => a.rw_rank - b.rw_rank,
  );

  return (
    <div className="space-y-4">
      <div className="bg-panel border border-edge rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="font-semibold">Joint hypothesis tests</h3>
          <div className="text-xs text-slate-500">
            {report.n_strategies_used} strategies · {report.n_days} aligned daily obs ·
            block_size={t.block_size} · n_boot={t.n_boot}
            {report.seed !== null && <> · seed={report.seed}</>}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
          <TestCard
            title="Reality Check (White 2000)"
            description="H0: no strategy in the family has positive mean return."
            stat={`max √T·μ = ${fmt(rc.test_stat, 3)}`}
            p={rc.p_value}
          />
          <TestCard
            title="SPA (Hansen 2005) — consistent"
            description="Studentized; recenters obviously-bad strategies. Use this as the headline p-value."
            stat={`max t = ${fmt(spa.test_stat_studentized, 3)}`}
            p={spa.p_value_consistent}
            extra={
              <div className="text-xs text-slate-500 mt-1">
                lower (≈RC): p={fmt(spa.p_value_lower, 3)} · upper: p={fmt(spa.p_value_upper, 3)}
                {" · "}kept (consistent): {spa.n_kept_consistent}/{report.n_strategies_used}
              </div>
            }
          />
          <TestCard
            title="Romano-Wolf — winners @ 5%"
            description="Per-strategy FWER-controlled p-values; lists rejected strategies."
            stat={`${rw.filter(r => r.reject_at_05).length}/${rw.length} reject`}
            p={Math.min(...rw.map(r => r.p_adj))}
            extra={
              <div className="text-xs text-slate-500 mt-1 truncate">
                @5%: {rw.filter(r => r.reject_at_05).map(r => r.strategy).join(", ") || "(none)"}
              </div>
            }
          />
        </div>

        <details className="text-xs text-slate-400">
          <summary className="cursor-pointer text-slate-300">
            What each p-value means (read once)
          </summary>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>
              <b>Reality Check p</b> = chance the best-of-N's observed
              mean would arise under "every strategy has zero edge", given
              the joint correlation structure. Low = the winner is real.
              Conservative when many bad strategies are in the universe.
            </li>
            <li>
              <b>SPA consistent p</b> = same null, but studentized
              (per-strategy scale) and obviously-bad strategies are
              excluded from the null sup. Recommended headline. Compare
              SPA lower/upper to gauge sensitivity to that exclusion.
            </li>
            <li>
              <b>Romano-Wolf p_adj</b> per strategy = FWER-controlled
              adjusted p-value. Each rejection is simultaneously
              significant at the chosen α across the entire family.
            </li>
          </ul>
        </details>
      </div>

      <PerStrategyTable rows={rows} />

      {payload.daily_returns && (
        <>
          <EquityCurves
            timestamp={payload.daily_returns.timestamp}
            curves={payload.daily_returns.equity_curves}
          />
          <CorrelationHeatmap
            matrix={report.correlation_matrix}
            order={Object.keys(payload.daily_returns.equity_curves)}
          />
        </>
      )}
    </div>
  );
}

function TestCard({
  title, description, stat, p, extra,
}: {
  title: string; description: string; stat: string; p: number;
  extra?: React.ReactNode;
}) {
  return (
    <div className="bg-bg/50 border border-edge rounded p-3">
      <div className="text-sm font-semibold mb-1">{title}</div>
      <div className="text-xs text-slate-400 mb-2">{description}</div>
      <div className="flex items-baseline gap-3">
        <div className={`text-2xl font-mono ${pClass(p)}`}>
          p = {fmt(p, 3)}
        </div>
        <div className="text-xs text-slate-500 font-mono">{stat}</div>
      </div>
      {extra}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function PerStrategyTable({ rows }: { rows: any[] }) {
  return (
    <div className="bg-panel border border-edge rounded-lg p-4">
      <h3 className="font-semibold mb-2">Per-strategy verdicts</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-slate-400 text-xs">
            <tr className="border-b border-edge">
              <th className="text-left px-2 py-1">rank</th>
              <th className="text-left px-2 py-1">strategy</th>
              <th className="text-right px-2 py-1">daily μ</th>
              <th className="text-right px-2 py-1">daily σ</th>
              <th className="text-right px-2 py-1">sharpe(daily)</th>
              <th className="text-right px-2 py-1">√T·μ</th>
              <th className="text-right px-2 py-1">RW p_adj</th>
              <th className="text-center px-2 py-1">verdict</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.strategy} className="border-b border-edge/40">
                <td className="px-2 py-1 font-mono text-slate-500">
                  #{r.rw_rank}
                </td>
                <td className="px-2 py-1 font-mono">{r.strategy}</td>
                <td className="px-2 py-1 text-right font-mono">
                  {fmtPct(r.mean, 3)}
                </td>
                <td className="px-2 py-1 text-right font-mono text-slate-500">
                  {fmtPct(r.std, 3)}
                </td>
                <td className="px-2 py-1 text-right font-mono">
                  {fmt(r.sharpe_per_period, 3)}
                </td>
                <td className="px-2 py-1 text-right font-mono">
                  {fmt(r.obs_stat_sqrtT_mean, 3)}
                </td>
                <td className={`px-2 py-1 text-right font-mono ${pClass(r.rw_p_adj)}`}>
                  {fmt(r.rw_p_adj, 3)}
                </td>
                <td className="px-2 py-1 text-center">
                  <VerdictPill
                    p={r.rw_p_adj}
                    label={r.rw_p_adj < 0.05 ? "REJECT H0" : r.rw_p_adj < 0.10 ? "marginal" : "fail"}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-slate-500 mt-2">
        REJECT H0 means: under FWER control across {rows.length} strategies,
        this strategy's mean return is significantly &gt; benchmark at 5%.
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function EquityCurves({
  timestamp, curves,
}: {
  timestamp: string[];
  curves: Record<string, number[]>;
}) {
  const traces = Object.entries(curves).map(([name, eq], i) => ({
    x: timestamp, y: eq, type: "scatter", mode: "lines",
    name, line: { color: COLORS[i % COLORS.length], width: 1.5 },
  }));
  return (
    <div className="bg-panel border border-edge rounded-lg p-4">
      <h3 className="font-semibold mb-2">OOS equity curves (re-based to 1.0)</h3>
      <Plot
        data={traces as any}
        layout={{
          autosize: true, height: 360,
          margin: { l: 50, r: 20, t: 10, b: 40 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { color: "#cbd5e1", size: 11 },
          xaxis: { gridcolor: "#1e293b" },
          yaxis: { gridcolor: "#1e293b", type: "log" },
          legend: { orientation: "h", y: -0.2 },
        } as any}
        config={{ displayModeBar: false, responsive: true } as any}
        style={{ width: "100%" }}
      />
      <div className="text-xs text-slate-500 mt-1">
        Daily OOS returns, compounded from $1 at OOS start. Aligned to
        the same calendar — gaps where one strategy has no data are
        dropped jointly (inner join).
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function CorrelationHeatmap({
  matrix, order,
}: {
  matrix: Record<string, Record<string, number | null>>;
  order: string[];
}) {
  const z = order.map((row) =>
    order.map((col) => {
      const v = matrix[row]?.[col];
      return v === null || v === undefined ? null : v;
    }),
  );
  return (
    <div className="bg-panel border border-edge rounded-lg p-4">
      <h3 className="font-semibold mb-2">Cross-strategy return correlation</h3>
      <Plot
        data={[{
          z, x: order, y: order, type: "heatmap",
          colorscale: "RdBu", zmin: -1, zmax: 1, reversescale: true,
          colorbar: { thickness: 12 },
          hovertemplate: "%{y} × %{x}: %{z:.2f}<extra></extra>",
        }] as any}
        layout={{
          autosize: true, height: 320,
          margin: { l: 120, r: 40, t: 10, b: 80 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { color: "#cbd5e1", size: 11 },
          xaxis: { tickangle: -30 },
        } as any}
        config={{ displayModeBar: false, responsive: true } as any}
        style={{ width: "100%" }}
      />
      <div className="text-xs text-slate-500 mt-1">
        Pearson correlation of daily OOS returns. High pairwise
        correlation reduces effective N for multiple-testing —
        the bootstrap preserves this structure under H0.
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function HistoryCard({ rows }: { rows: MultiStratListEntry[] }) {
  return (
    <div className="bg-panel border border-edge rounded-lg p-4">
      <h3 className="font-semibold mb-2">Past runs</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-slate-400 text-xs">
            <tr className="border-b border-edge">
              <th className="text-left px-2 py-1">ran at</th>
              <th className="text-right px-2 py-1">N strats</th>
              <th className="text-right px-2 py-1">N days</th>
              <th className="text-right px-2 py-1">RC p</th>
              <th className="text-right px-2 py-1">SPA p</th>
              <th className="text-right px-2 py-1">RW rejects@5%</th>
              <th className="text-left px-2 py-1">file</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.file} className="border-b border-edge/40">
                <td className="px-2 py-1 font-mono text-slate-400">
                  {r.ran_at?.slice(0, 19).replace("T", " ")}
                </td>
                <td className="px-2 py-1 text-right font-mono">
                  {r.n_strategies_used}
                </td>
                <td className="px-2 py-1 text-right font-mono">
                  {r.n_days}
                </td>
                <td className={`px-2 py-1 text-right font-mono ${pClass(r.reality_check_p)}`}>
                  {fmt(r.reality_check_p, 3)}
                </td>
                <td className={`px-2 py-1 text-right font-mono ${pClass(r.spa_p_consistent)}`}>
                  {fmt(r.spa_p_consistent, 3)}
                </td>
                <td className="px-2 py-1 text-right font-mono">
                  {r.n_reject_at_05}
                </td>
                <td className="px-2 py-1 font-mono text-xs text-slate-500">
                  {r.file}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
