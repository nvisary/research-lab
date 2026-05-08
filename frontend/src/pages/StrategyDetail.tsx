import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  type EquityCurve,
  type HoldoutReport,
  type Job,
  type StrategyDetail as Detail,
} from "../api";
import { fmt, fmtPct, verdictClass } from "../format";
import { EquityChart } from "../components/EquityChart";

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
  tf: "1h",
  walk: 0,
  note: "",
};

const HOLDOUT = { start: "2025-10-01", end: "2026-05-01", tf: "1h" };

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
  const pollTimer = useRef<number | null>(null);
  const holdoutPollTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.strategy(name);
      setDetail(d);
      if (d.history.length > 0) {
        setSelectedIter((prev) => prev ?? d.history[d.history.length - 1].iter);
      }
    } catch (e) {
      setError(String(e));
    }
  }, [name]);

  useEffect(() => {
    load();
    api.holdoutReport(name).then(setHoldout).catch(() => setHoldout(null));
  }, [load, name]);

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

  const reversedHistory = useMemo(() => detail?.history.slice().reverse() ?? [], [detail]);

  if (error) return <div className="text-rose-400">{error}</div>;
  if (!detail) return <div className="text-slate-500">loading…</div>;

  const best = detail.best;
  return (
    <>
      <h1 className="text-2xl font-semibold mb-4">
        <Link to="/" className="text-slate-400 hover:text-slate-100">
          strategies
        </Link>{" "}
        / <span>{name}</span>
      </h1>

      <Card title="Best">
        {!best ? (
          <em className="text-slate-500">no successful iterations yet</em>
        ) : (
          <table className="text-sm">
            <tbody>
              <KV k="iter" v={best.iter} />
              <KV k="composite" v={<strong>{fmt(best.composite)}</strong>} />
              <KV k="params" v={<code className="text-slate-300">{JSON.stringify(best.params)}</code>} />
              <KV k="train sharpe" v={fmt(best.metrics?.train?.sharpe)} />
              <KV k="OOS sharpe" v={fmt(best.metrics?.oos?.sharpe)} />
              <KV k="OOS max DD" v={fmtPct(best.metrics?.oos?.max_dd)} />
              <KV k="OOS trades" v={best.metrics?.oos?.n_trades ?? "—"} />
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
              className="input w-16"
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
        {reversedHistory.length === 0 ? (
          <em className="text-slate-500">no iterations yet</em>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <Th>iter</Th>
                  <Th>verdict</Th>
                  <Th>composite</Th>
                  <Th>OOS sharpe</Th>
                  <Th>OOS max DD</Th>
                  <Th>OOS trades</Th>
                  <Th>note</Th>
                  <Th>finished</Th>
                </tr>
              </thead>
              <tbody>
                {reversedHistory.map((h) => (
                  <tr key={h.iter} className="border-t border-edge">
                    <td className="px-3 py-1.5">{h.iter}</td>
                    <td className="px-3 py-1.5">
                      <span className={`px-2 py-0.5 rounded text-xs ${verdictClass(h.verdict)}`}>
                        {h.verdict}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 mono">{fmt(h.composite)}</td>
                    <td className="px-3 py-1.5 mono">{fmt(h.metrics_oos?.sharpe)}</td>
                    <td className="px-3 py-1.5 mono">{fmtPct(h.metrics_oos?.max_dd)}</td>
                    <td className="px-3 py-1.5 mono">{h.metrics_oos?.n_trades ?? "—"}</td>
                    <td className="px-3 py-1.5 text-slate-300">{h.note || ""}</td>
                    <td className="px-3 py-1.5 text-slate-500 text-xs">
                      {h.finished ? new Date(h.finished).toLocaleString() : ""}
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
  return (
    <tr>
      <th className="text-left text-slate-400 font-medium pr-4 py-1 align-top w-32">{k}</th>
      <td className="py-1">{v}</td>
    </tr>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="text-left px-3 py-2 font-medium">{children}</th>;
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
