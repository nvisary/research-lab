import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  type EquityCurve,
  type HoldoutReport,
  type Job,
  type StrategyDetail as Detail,
} from "../api";
import { fmt, fmtPct, probabilityClass, verdictClass } from "../format";
import { helpFor } from "../metricsHelp";
import { EquityChart } from "../components/EquityChart";
import { DrawdownChart } from "../components/DrawdownChart";
import { PriceChart } from "../components/PriceChart";
import { MonthlyReturnsHeatmap } from "../components/MonthlyReturnsHeatmap";
import { QualityIndicators } from "../components/QualityIndicators";
import { TradesCard } from "../components/TradesCard";
import { Tooltip } from "../components/Tooltip";

type IterForm = {
  start: string;
  end: string;
  tf: string;
  walk: number;
  note: string;
};

const defaultForm: IterForm = {
  start: "2024-01-01",
  end: "2025-10-01",
  // Empty string => backend defers to strategy.py:DEFAULT_TF. The user can
  // override by typing a value here.
  tf: "",
  walk: 4,
  note: "",
};

const HOLDOUT = { start: "2025-10-01", end: "2026-05-01", tf: "" };

export function StrategyDetail() {
  const { name = "" } = useParams();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedIter, setSelectedIter] = useState<number | null>(null);
  const [overlay, setOverlay] = useState(false);
  const [curves, setCurves] = useState<{ iter: number; verdict: string; data: EquityCurve }[]>([]);
  const [form, setForm] = useState<IterForm>(defaultForm);
  const [job, setJob] = useState<Job | null>(null);
  const [holdout, setHoldout] = useState<HoldoutReport>(null);
  const [holdoutJob, setHoldoutJob] = useState<Job | null>(null);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({
    key: "iter", dir: "desc",
  });
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const pollTimer = useRef<number | null>(null);
  const holdoutPollTimer = useRef<number | null>(null);
  const autoRefreshTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [d, h] = await Promise.all([
        api.strategy(name),
        api.holdoutReport(name).catch(() => null),
      ]);
      setDetail(d);
      setHoldout(h);
      if (d.history.length > 0) {
        setSelectedIter((prev) => prev ?? d.history[d.history.length - 1].iter);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }, [name]);

  useEffect(() => {
    load();
  }, [load]);

  // Optional auto-refresh: re-fetch detail every 5s while enabled.
  useEffect(() => {
    if (!autoRefresh) return;
    const tick = () => {
      load();
      autoRefreshTimer.current = window.setTimeout(tick, 5000);
    };
    autoRefreshTimer.current = window.setTimeout(tick, 5000);
    return () => {
      if (autoRefreshTimer.current) {
        window.clearTimeout(autoRefreshTimer.current);
        autoRefreshTimer.current = null;
      }
    };
  }, [autoRefresh, load]);

  // Load equity curves whenever selection or overlay flag changes.
  useEffect(() => {
    if (!detail || selectedIter === null) {
      setCurves([]);
      return;
    }
    const targets = overlay ? detail.history.slice(-5) : detail.history.filter((h) => h.iter === selectedIter);
    let cancelled = false;
    (async () => {
      const out: { iter: number; verdict: string; data: EquityCurve }[] = [];
      for (const h of targets) {
        try {
          const eq = await api.equity(name, h.iter);
          out.push({ iter: h.iter, verdict: h.verdict, data: eq });
        } catch {
          /* missing equity for this iter — skip */
        }
      }
      if (!cancelled) setCurves(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [detail, selectedIter, overlay, name]);

  const pollJob = useCallback(
    async (id: string) => {
      try {
        const j = await api.job(id);
        setJob(j);
        if (j.status === "done" || j.status === "failed") {
          if (pollTimer.current) {
            window.clearTimeout(pollTimer.current);
            pollTimer.current = null;
          }
          await load();
          return;
        }
        pollTimer.current = window.setTimeout(() => pollJob(id), 1000);
      } catch (e) {
        setError(String(e));
      }
    },
    [load]
  );

  useEffect(() => {
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      if (holdoutPollTimer.current) window.clearTimeout(holdoutPollTimer.current);
    };
  }, []);

  const pollHoldoutJob = useCallback(
    async (id: string) => {
      try {
        const j = await api.job(id);
        setHoldoutJob(j);
        if (j.status === "done" || j.status === "failed") {
          if (holdoutPollTimer.current) {
            window.clearTimeout(holdoutPollTimer.current);
            holdoutPollTimer.current = null;
          }
          const rep = await api.holdoutReport(name).catch(() => null);
          setHoldout(rep);
          return;
        }
        holdoutPollTimer.current = window.setTimeout(() => pollHoldoutJob(id), 1000);
      } catch (e) {
        setError(String(e));
      }
    },
    [name]
  );

  const runHoldout = async () => {
    setHoldoutJob(null);
    try {
      const j = await api.holdout(name, HOLDOUT);
      setHoldoutJob(j);
      pollHoldoutJob(j.id);
    } catch (e) {
      setError(String(e));
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setJob(null);
    setError(null);
    try {
      const j = await api.iterate(name, form);
      setJob(j);
      pollJob(j.id);
    } catch (e) {
      setError(String(e));
    }
  };

  const sortedHistory = useMemo(() => {
    const rows = detail?.history.slice() ?? [];
    const valueOf = (h: typeof rows[number], key: string): number | string | null => {
      switch (key) {
        case "iter": return h.iter;
        case "verdict": return h.verdict;
        case "composite": return h.composite ?? -Infinity;
        case "OOS sharpe": return h.metrics_oos?.sharpe ?? -Infinity;
        case "OOS max DD": return h.metrics_oos?.max_dd ?? Infinity;
        case "OOS trades": return h.metrics_oos?.n_trades ?? -Infinity;
        case "DSR": return h.dsr ?? -Infinity;
        case "note": return h.note ?? "";
        case "finished": return h.finished ? new Date(h.finished).getTime() : 0;
        default: return 0;
      }
    };
    const sign = sort.dir === "asc" ? 1 : -1;
    return rows.sort((a, b) => {
      const va = valueOf(a, sort.key);
      const vb = valueOf(b, sort.key);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * sign;
      return String(va).localeCompare(String(vb)) * sign;
    });
  }, [detail, sort]);

  const onSort = (key: string) =>
    setSort((s) => (s.key === key
      ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
      : { key, dir: "desc" }));

  if (error) return <div className="text-rose-400">{error}</div>;
  if (!detail) return <div className="text-slate-500">loading…</div>;

  const best = detail.best;
  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">
          <Link to="/" className="text-slate-400 hover:text-slate-100">
            strategies
          </Link>{" "}
          / <span>{name}</span>
        </h1>
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-400 inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            auto-refresh (5s)
          </label>
          <button
            onClick={() => load()}
            disabled={refreshing}
            className="text-xs px-3 py-1.5 rounded border border-edge bg-slate-800 hover:bg-slate-700 disabled:opacity-50"
            title="Re-fetch best.json, history, equity, holdout. Use after a long-running iter completes."
          >
            {refreshing ? "refreshing…" : "↻ refresh"}
          </button>
        </div>
      </div>

      <Card title="Best">
        {!best ? (
          <em className="text-slate-500">no successful iterations yet</em>
        ) : (
          <table className="text-sm">
            <tbody>
              <KV
                k="iter"
                v={
                  <>
                    {best.iter}
                    <a
                      href={`/api/strategies/${name}/tearsheet/${best.iter}`}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-3 text-xs text-blue-400 hover:text-blue-300"
                    >
                      tear sheet ↗
                    </a>
                  </>
                }
              />
              <KV k="composite" v={<strong>{fmt(best.composite)}</strong>} />
              <KV
                k="DSR"
                v={
                  <span className={probabilityClass(best.dsr)} title="Deflated Sharpe Ratio: probability of true edge given the number of trials.">
                    {fmt(best.dsr, 3)}
                  </span>
                }
              />
              <KV k="params" v={<code className="text-slate-300">{JSON.stringify(best.params)}</code>} />
              {best.wf_aggregate ? (
                <>
                  <KV
                    k="WF OOS sharpe"
                    v={
                      <>
                        mean <strong>{fmt(best.wf_aggregate.mean_sharpe)}</strong>{" "}
                        ± {fmt(best.wf_aggregate.std_sharpe)} std,{" "}
                        median {fmt(best.wf_aggregate.median_sharpe)}
                      </>
                    }
                  />
                  {best.wf_aggregate.mean_alpha_sharpe !== null && best.wf_aggregate.mean_alpha_sharpe !== undefined && (
                    <KV
                      k="WF alpha (vs b&h)"
                      v={
                        <>
                          <strong className={
                            best.wf_aggregate.mean_alpha_sharpe > 0.1
                              ? "text-emerald-400"
                              : best.wf_aggregate.mean_alpha_sharpe < -0.1
                                ? "text-rose-400"
                                : "text-amber-400"
                          }>
                            {fmt(best.wf_aggregate.mean_alpha_sharpe)}
                          </strong>
                          {" "}sharpe
                          {" "}<span className="text-slate-500">
                            (b&h mean {fmt(best.wf_aggregate.mean_bench_sharpe)})
                          </span>
                          {best.wf_aggregate.window_alphas && (
                            <div className="text-xs text-slate-500 mono mt-0.5">
                              per-window: [{best.wf_aggregate.window_alphas
                                .map((a) => a == null ? "—" : a.toFixed(2)).join(", ")}]
                            </div>
                          )}
                        </>
                      }
                    />
                  )}
                  <KV
                    k="WF CAGR"
                    v={
                      <>
                        mean <strong>{fmtPct(best.wf_aggregate.mean_cagr)}</strong> ·{" "}
                        median {fmtPct(best.wf_aggregate.median_cagr)}{" "}
                        <span className="text-slate-500">(annualized per window)</span>
                      </>
                    }
                  />
                  <KV
                    k="WF return / window"
                    v={<>mean {fmtPct(best.wf_aggregate.mean_total_return)} <span className="text-slate-500">over the OOS slice (~{(21 / (best.wf_aggregate.n_windows || 4) * 0.25).toFixed(1)}mo)</span></>}
                  />
                  <KV k="WF max DD" v={<>worst {fmtPct(best.wf_aggregate.worst_max_dd)} • mean {fmtPct(best.wf_aggregate.mean_max_dd)}</>} />
                  <KV k="WF windows" v={best.wf_aggregate.n_windows} />
                  {best.wf_aggregate.window_composites && (
                    <KV
                      k="per-window composite"
                      v={
                        <span className="mono text-slate-300">
                          [{best.wf_aggregate.window_composites.map((c) => fmt(c, 2)).join(", ")}]
                        </span>
                      }
                    />
                  )}
                </>
              ) : (
                <>
                  <KV k="train sharpe" v={fmt(best.metrics?.train?.sharpe)} />
                  <KV k="OOS sharpe" v={fmt(best.metrics?.oos?.sharpe)} />
                  <KV k="OOS CAGR" v={fmtPct(best.metrics?.oos?.cagr)} />
                  <KV k="OOS total return" v={fmtPct(best.metrics?.oos?.total_return)} />
                  <KV k="OOS max DD" v={fmtPct(best.metrics?.oos?.max_dd)} />
                  <KV k="OOS trades" v={best.metrics?.oos?.n_trades ?? "—"} />
                </>
              )}
              <KV k="symbols" v={best.symbols.join(", ")} />
              <KV k="period" v={best.period.join(" → ")} />
              <KV k="note" v={best.note || "—"} />
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Holdout sanity check (2025-10 → 2026-04, never seen by iteration)">
        <div className="flex items-start gap-6 flex-wrap">
          <div className="flex-1 min-w-[260px]">
            {!holdout?.report ? (
              <em className="text-slate-500">no holdout report yet — run one to validate the current best</em>
            ) : (
              <table className="text-sm">
                <tbody>
                  <KV k="for iter" v={holdout.report.iter} />
                  <KV k="period" v={holdout.report.period.join(" → ")} />
                  <KV
                    k="composite"
                    v={
                      <>
                        <strong>{fmt(holdout.report.composite)}</strong>
                        {holdout.report.best_composite_train_val !== null && (
                          <span className="text-slate-500 ml-2">
                            (train+val best: {fmt(holdout.report.best_composite_train_val)})
                          </span>
                        )}
                      </>
                    }
                  />
                  <KV k="sharpe" v={fmt(holdout.report.metrics?.sharpe)} />
                  <KV k="sortino" v={fmt(holdout.report.metrics?.sortino)} />
                  <KV k="max DD" v={fmtPct(holdout.report.metrics?.max_dd)} />
                  <KV k="trades" v={holdout.report.metrics?.n_trades ?? "—"} />
                  <KV k="ran at" v={new Date(holdout.report.ran_at).toLocaleString()} />
                </tbody>
              </table>
            )}
          </div>
          <div className="flex flex-col items-end gap-2">
            <button
              onClick={runHoldout}
              disabled={holdoutJob?.status === "running"}
              className="bg-amber-600 hover:bg-amber-500 disabled:bg-slate-600 px-4 py-1.5 rounded font-semibold"
            >
              {holdoutJob?.status === "running" ? "running…" : "run holdout"}
            </button>
            <span className="text-xs text-slate-500 text-right max-w-[200px]">
              uses current <code className="text-slate-400">strategy.py</code> on 2025-Q4. Does not change <code className="text-slate-400">best.json</code>.
            </span>
          </div>
        </div>
        {holdoutJob && (
          <pre className="mt-3 bg-slate-950 text-slate-300 text-xs p-3 rounded max-h-60 overflow-auto whitespace-pre-wrap">
            {`[${holdoutJob.status}${holdoutJob.exit_code !== null ? ` exit=${holdoutJob.exit_code}` : ""}]\n${holdoutJob.tail.join("\n")}`}
          </pre>
        )}
      </Card>

      <Card title="Equity curve">
        <div className="flex items-center gap-4 mb-3 flex-wrap">
          <label className="text-slate-400">
            iter:&nbsp;
            <select
              value={selectedIter ?? ""}
              onChange={(e) => setSelectedIter(parseInt(e.target.value))}
              className="bg-slate-900 border border-edge rounded px-2 py-1 text-slate-100"
            >
              {detail.history.map((h) => (
                <option key={h.iter} value={h.iter}>
                  {h.iter} — {h.verdict} — {fmt(h.composite)}
                </option>
              ))}
            </select>
          </label>
          <label className="text-slate-400">
            <input
              type="checkbox"
              className="mr-1"
              checked={overlay}
              onChange={(e) => setOverlay(e.target.checked)}
            />
            overlay last 5
          </label>
        </div>
        <EquityChart curves={curves} highlightIter={selectedIter ?? undefined} />
      </Card>

      <Card title="Drawdown (underwater)">
        <DrawdownChart curves={curves} highlightIter={selectedIter ?? undefined} />
      </Card>

      {best && (
        <Card title="Price chart with trade markers">
          <PriceChart
            strategy={name}
            iter={selectedIter}
            symbols={best.symbols}
            start={best.period[0]}
            end={best.period[1]}
            tf={best.tf}
          />
        </Card>
      )}

      {best && (
        <Card title="Quality indicators">
          <QualityIndicators
            wf={best.wf_aggregate}
            oos={best.metrics?.oos}
            strategy={name}
            iter={selectedIter}
          />
        </Card>
      )}

      <Card title="Monthly returns">
        <MonthlyReturnsHeatmap strategy={name} iter={selectedIter} />
      </Card>

      <Card title="Trades">
        <TradesCard strategy={name} iter={selectedIter} />
      </Card>

      <Card title="Run new iteration">
        <form onSubmit={submit} className="flex items-end gap-3 flex-wrap">
          <Field label="start">
            <input
              value={form.start}
              onChange={(e) => setForm({ ...form, start: e.target.value })}
              className="input w-28"
            />
          </Field>
          <Field label="end">
            <input
              value={form.end}
              onChange={(e) => setForm({ ...form, end: e.target.value })}
              className="input w-28"
            />
          </Field>
          <Field label="tf">
            <input
              value={form.tf}
              onChange={(e) => setForm({ ...form, tf: e.target.value })}
              placeholder={detail.best?.tf || "auto"}
              title="Empty = use strategy.py:DEFAULT_TF. Override here only if you know what you're doing."
              className="input w-20"
            />
          </Field>
          <Field label="walk">
            <input
              type="number"
              min={0}
              max={12}
              value={form.walk}
              onChange={(e) => setForm({ ...form, walk: parseInt(e.target.value || "0") })}
              className="input w-16"
            />
          </Field>
          <Field label="note" grow>
            <input
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
              className="input w-full"
              placeholder="what did you change?"
            />
          </Field>
          <button
            type="submit"
            disabled={job?.status === "running"}
            className="bg-blue-500 hover:bg-blue-400 disabled:bg-slate-600 px-4 py-1.5 rounded font-semibold"
          >
            {job?.status === "running" ? "running…" : "run"}
          </button>
        </form>
        {job && (
          <pre className="mt-3 bg-slate-950 text-slate-300 text-xs p-3 rounded max-h-80 overflow-auto whitespace-pre-wrap">
            {`[${job.status}${job.exit_code !== null ? ` exit=${job.exit_code}` : ""}] ${job.cmd.join(" ")}\n\n${job.tail.join("\n")}`}
          </pre>
        )}
      </Card>

      <Card title="History">
        {sortedHistory.length === 0 ? (
          <em className="text-slate-500">no iterations yet</em>
        ) : null}
        {sortedHistory.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  {(["iter","verdict","composite","OOS sharpe","OOS max DD","OOS trades","DSR","note","finished"] as const).map((k) => (
                    <SortableTh key={k} label={k} sort={sort} onSort={onSort} />
                  ))}
                  <Th help="Open the standalone HTML tear sheet for this iteration in a new tab.">📊</Th>
                </tr>
              </thead>
              <tbody>
                {sortedHistory.map((h) => (
                  <tr key={h.iter} className="border-t border-edge">
                    <td className="px-3 py-1.5">{h.iter}</td>
                    <td className="px-3 py-1.5">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${verdictClass(h.verdict)}`}
                        title={h.audit?.message || h.error || undefined}
                      >
                        {h.verdict}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 mono">{fmt(h.composite)}</td>
                    <td className="px-3 py-1.5 mono">{fmt(h.metrics_oos?.sharpe)}</td>
                    <td className="px-3 py-1.5 mono">{fmtPct(h.metrics_oos?.max_dd)}</td>
                    <td className="px-3 py-1.5 mono">{h.metrics_oos?.n_trades ?? "—"}</td>
                    <td className={`px-3 py-1.5 mono ${probabilityClass(h.dsr)}`}>{fmt(h.dsr, 3)}</td>
                    <td className="px-3 py-1.5 text-slate-300">{h.note || ""}</td>
                    <td className="px-3 py-1.5 text-slate-500 text-xs">
                      {h.finished ? new Date(h.finished).toLocaleString() : ""}
                    </td>
                    <td className="px-3 py-1.5">
                      {(h.verdict === "KEEP" || h.verdict === "BASELINE") ? (
                        <a
                          href={`/api/strategies/${name}/tearsheet/${h.iter}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-400 hover:text-blue-300 underline"
                        >
                          open
                        </a>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="strategy.py">
        <pre className="bg-slate-950 text-slate-300 text-xs p-3 rounded max-h-[480px] overflow-auto">
          {detail.strategy_py}
        </pre>
      </Card>

      <style>{`.input{background:#0f172a;border:1px solid #334155;border-radius:4px;padding:5px 8px;color:#e2e8f0;font:inherit}`}</style>
    </>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-edge bg-panel p-4 mb-4">
      <h2 className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-3">{title}</h2>
      {children}
    </section>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  const help = helpFor(k);
  return (
    <tr>
      <th className="text-left text-slate-400 font-medium pr-4 py-1 align-top w-44">
        {help ? <Tooltip text={help}>{k}</Tooltip> : k}
      </th>
      <td className="py-1">{v}</td>
    </tr>
  );
}

function SortableTh({ label, sort, onSort }:
  { label: string; sort: { key: string; dir: "asc" | "desc" }; onSort: (k: string) => void }
) {
  const active = sort.key === label;
  const arrow = active ? (sort.dir === "asc" ? "↑" : "↓") : "";
  const help = helpFor(label);
  const inner = (
    <button
      type="button"
      onClick={() => onSort(label)}
      className={`inline-flex items-center gap-1 cursor-pointer hover:text-slate-100
                  ${active ? "text-blue-400" : ""}`}
    >
      {label}
      <span className="text-[10px] w-3">{arrow}</span>
    </button>
  );
  return (
    <th className="text-left px-3 py-2 font-medium select-none">
      {help ? <Tooltip text={help}>{inner}</Tooltip> : inner}
    </th>
  );
}


function Th({ children, help }: { children: React.ReactNode; help?: string }) {
  const text = help ?? (typeof children === "string" ? helpFor(children) : undefined);
  return (
    <th className="text-left px-3 py-2 font-medium">
      {text ? <Tooltip text={text}>{children}</Tooltip> : children}
    </th>
  );
}

function Field({
  label,
  children,
  grow,
}: {
  label: string;
  children: React.ReactNode;
  grow?: boolean;
}) {
  return (
    <label className={`flex flex-col gap-1 text-slate-400 text-xs ${grow ? "flex-1 min-w-[200px]" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}
