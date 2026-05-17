/**
 * ForwardCard — post-holdout / live-paper analogue diagnostic.
 *
 * Forward-test runs the locked best snapshot on bars that came AFTER
 * the holdout window ended. It's the honest "deploy and watch" proxy
 * for live performance without an execution layer.
 *
 * Card shows:
 *   - Drift flag pill (ok / warn / alert / unknown) with rationale.
 *   - Side-by-side comparison: backtest CI vs realised forward Sharpe.
 *   - Forward equity curve with rolling-30d Sharpe overlay.
 *   - Run button (background job).
 */
import { useEffect, useState } from "react";
import factoryImport from "react-plotly.js/factory";
// @ts-expect-error — no types for the dist-min bundle
import Plotly from "plotly.js-basic-dist-min";
import {
  api,
  type ForwardPayload,
  type ForwardDrift,
  type Job,
} from "../api";
import { fmt, fmtPct } from "../format";

const createPlotlyComponent =
  (factoryImport as any).default ?? (factoryImport as any);
const Plot = createPlotlyComponent(Plotly);


function FlagPill({ flag }: { flag: ForwardDrift["flag"] }) {
  const map: Record<string, string> = {
    ok: "bg-emerald-500/15 text-emerald-300",
    warn: "bg-amber-500/15 text-amber-300",
    alert: "bg-rose-500/15 text-rose-300",
    unknown: "bg-slate-500/15 text-slate-400",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono uppercase ${map[flag]}`}>
      {flag}
    </span>
  );
}


function CIVisual({ drift }: { drift: ForwardDrift }) {
  const lo = drift.backtest_sharpe_ci_lo;
  const hi = drift.backtest_sharpe_ci_hi;
  const bt = drift.backtest_sharpe;
  const fwd = drift.forward_sharpe;
  if (lo === null || hi === null || bt === null) {
    return (
      <div className="text-xs text-slate-500">
        No backtest CI in best.json — cannot anchor drift.
      </div>
    );
  }
  const min = Math.min(lo, fwd, -0.5);
  const max = Math.max(hi, fwd, 0.5);
  const pct = (x: number) => ((x - min) / (max - min)) * 100;
  return (
    <div>
      <div className="relative h-8 bg-bg/40 border border-edge rounded">
        {/* CI bar */}
        <div
          className="absolute top-1 bottom-1 bg-emerald-500/15 border border-emerald-500/30 rounded"
          style={{ left: `${pct(lo)}%`, width: `${pct(hi) - pct(lo)}%` }}
        />
        {/* Backtest Sharpe marker */}
        <div
          className="absolute top-0 bottom-0 w-px bg-emerald-300"
          style={{ left: `${pct(bt)}%` }}
          title={`Backtest Sharpe ${fmt(bt, 3)}`}
        />
        {/* Forward Sharpe marker */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-amber-300"
          style={{ left: `${pct(fwd)}%` }}
          title={`Forward Sharpe ${fmt(fwd, 3)}`}
        />
        {/* Zero line */}
        <div
          className="absolute top-2 bottom-2 w-px bg-slate-500/50"
          style={{ left: `${pct(0)}%` }}
          title="zero"
        />
      </div>
      <div className="flex justify-between text-xs text-slate-500 mt-1 font-mono">
        <span>{fmt(min, 2)}</span>
        <span className="text-slate-400">
          backtest CI [{fmt(lo, 2)}, {fmt(hi, 2)}] · backtest {fmt(bt, 2)} · forward {fmt(fwd, 2)}
        </span>
        <span>{fmt(max, 2)}</span>
      </div>
    </div>
  );
}


function EquityPlot({ payload }: { payload: ForwardPayload }) {
  if (!payload.equity || payload.equity.equity.length === 0) {
    return null;
  }
  const { timestamp, equity, benchmark, rolling_sharpe_30d } = payload.equity;
  const traces: any[] = [
    {
      x: timestamp, y: equity, type: "scatter", mode: "lines",
      name: "forward equity", yaxis: "y",
      line: { color: "#60a5fa", width: 1.5 },
    },
    {
      x: timestamp, y: benchmark, type: "scatter", mode: "lines",
      name: "benchmark (BH)", yaxis: "y",
      line: { color: "#64748b", width: 1, dash: "dot" },
    },
  ];
  if (rolling_sharpe_30d) {
    traces.push({
      x: timestamp, y: rolling_sharpe_30d, type: "scatter", mode: "lines",
      name: "rolling 30d sharpe", yaxis: "y2",
      line: { color: "#f59e0b", width: 1 },
    });
  }
  return (
    <Plot
      data={traces}
      layout={{
        autosize: true, height: 320,
        margin: { l: 60, r: 60, t: 10, b: 30 },
        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
        font: { color: "#cbd5e1", size: 11 },
        xaxis: { gridcolor: "#1e293b" },
        yaxis: {
          gridcolor: "#1e293b", title: { text: "equity ($)", standoff: 6 },
        },
        yaxis2: {
          overlaying: "y", side: "right",
          gridcolor: "transparent",
          title: { text: "30d sharpe", standoff: 6 },
          showgrid: false,
        },
        legend: { orientation: "h", y: -0.15 },
      } as any}
      config={{ displayModeBar: false, responsive: true } as any}
      style={{ width: "100%" }}
    />
  );
}


export function ForwardCard({ strategy }: { strategy: string }) {
  const [payload, setPayload] = useState<ForwardPayload | null | undefined>(undefined);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  async function load() {
    try {
      const p = await api.forwardLatest(strategy);
      setPayload(p);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  useEffect(() => {
    setPayload(undefined);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        const j = await api.job(job.id);
        setJob(j);
        if (j.status === "done") load();
      } catch {/* swallow */}
    }, 1500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job]);

  async function run() {
    setError(null);
    try {
      const j = await api.forwardRun(strategy, {
        start: start || null,
        end: end || null,
      });
      setJob(j);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  if (payload === undefined) {
    return (
      <div className="text-sm text-slate-500">loading forward report…</div>
    );
  }

  const drift = payload?.report?.drift;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={start}
          onChange={(e) => setStart(e.target.value)}
          placeholder="start (default: after holdout)"
          className="bg-bg border border-edge rounded px-2 py-1 text-xs font-mono"
        />
        <input
          value={end}
          onChange={(e) => setEnd(e.target.value)}
          placeholder="end (default: today − 1d)"
          className="bg-bg border border-edge rounded px-2 py-1 text-xs font-mono"
        />
        <button
          onClick={run}
          disabled={job?.status === "running" || job?.status === "pending"}
          className="bg-emerald-500/20 hover:bg-emerald-500/30 disabled:opacity-50 text-emerald-300 border border-emerald-500/40 rounded px-3 py-1 text-sm"
        >
          {job?.status === "running" || job?.status === "pending"
            ? "running…"
            : "run forward-test"}
        </button>
        {job && (
          <span className="text-xs text-slate-500 font-mono">
            job {job.id} · {job.status}
          </span>
        )}
      </div>

      {error && (
        <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded p-2 text-xs">
          {error}
        </div>
      )}
      {job && job.tail.length > 0 && job.status !== "done" && (
        <pre className="bg-bg border border-edge rounded p-2 text-xs max-h-32 overflow-y-auto whitespace-pre-wrap">
          {job.tail.slice(-20).join("\n")}
        </pre>
      )}

      {payload === null && (
        <div className="text-sm text-slate-500">
          No forward-test run yet. Press <b>run</b> to compute the locked best
          on post-holdout bars. The snapshot at{" "}
          <code className="font-mono">runs/best_strategy.py</code> is used
          when available.
        </div>
      )}

      {payload && drift && (
        <>
          <div className="flex items-center gap-3 flex-wrap">
            <FlagPill flag={drift.flag} />
            <span className="text-xs text-slate-400">
              {payload.report.period?.[0]} → {payload.report.period?.[1]} ·
              iter {payload.report.iter} ·
              {payload.report.snapshot_used
                ? " using best_strategy.py snapshot"
                : " using current strategy.py (no snapshot found)"}
            </span>
          </div>

          <div className="text-sm text-slate-300">
            {drift.flag_reason}
          </div>

          <CIVisual drift={drift} />

          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
            <Stat label="forward Sharpe"
                  value={fmt(drift.forward_sharpe, 3)} />
            <Stat label="forward return"
                  value={fmtPct(drift.forward_total_return, 2)} />
            <Stat label="forward MaxDD"
                  value={fmtPct(drift.forward_max_dd, 2)} />
            <Stat label="forward PSR"
                  value={drift.forward_psr === null
                    ? "—" : fmt(drift.forward_psr, 3)} />
            <Stat label="below-CI streak"
                  value={`${drift.consecutive_below_ci_days}d`} />
          </div>

          <EquityPlot payload={payload} />
        </>
      )}

      <details className="text-xs text-slate-400 mt-2">
        <summary className="cursor-pointer text-slate-300">
          What is forward-test?
        </summary>
        <p className="mt-2">
          The strategy code is locked when it became <b>best</b>. Forward-test
          runs that locked snapshot on bars that came AFTER the holdout window
          ended — bars the strategy and the operator have never seen and
          never used to tune anything. The drift flag compares realised
          forward Sharpe to the backtest's OOS Sharpe CI: if forward sits
          deep below CI lower, or has been below for 14+ consecutive days
          (rolling-30d Sharpe), the alpha has likely decayed. Forward PSR
          (probability forward Sharpe is &gt; 0) under 0.5 means the result
          is statistically indistinguishable from luck.
        </p>
      </details>
    </div>
  );
}


function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg/30 border border-edge rounded p-2">
      <div className="text-slate-500">{label}</div>
      <div className="font-mono text-slate-200">{value}</div>
    </div>
  );
}
