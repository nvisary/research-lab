/**
 * MetaLabelerCard — surfaces the meta-classifier diagnostic from the
 * current best iter, if the strategy exports META_LABELER. Renders:
 *   - status pill (ok / skipped / failed) per window
 *   - core metrics (n_events, class balance, accuracy, precision/recall)
 *   - feature importances bar
 *   - the list of features used + threshold / mode
 *
 * Shows nothing (returns null) when the strategy has no meta-labeling.
 */
import { useEffect, useState } from "react";
import { api, type MetaWindowReport, type StrategyMetaPayload } from "../api";
import { fmt, fmtPct } from "../format";

function StatusPill({ status }: { status: string }) {
  const cls = status === "ok"
    ? "bg-emerald-500/15 text-emerald-300"
    : status === "failed"
      ? "bg-rose-500/10 text-rose-400"
      : "bg-amber-500/10 text-amber-400";
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono ${cls}`}>
      {status}
    </span>
  );
}

function WindowBlock({ w, idx }: { w: MetaWindowReport; idx: number | null }) {
  if (w.status !== "ok") {
    return (
      <div className="bg-bg/40 border border-edge rounded p-2 text-sm">
        <div className="flex items-center gap-2 mb-1">
          {idx !== null && <span className="text-xs text-slate-500">window {idx}</span>}
          <StatusPill status={w.status} />
        </div>
        <div className="text-xs text-slate-400">
          {w.reason || w.error || "(no detail)"}
        </div>
      </div>
    );
  }
  const importances = Object.entries(w.feature_importances || {})
    .sort((a, b) => b[1] - a[1]);
  const maxImp = importances.length ? Math.max(...importances.map(([_, v]) => v)) : 1;
  return (
    <div className="bg-bg/40 border border-edge rounded p-3 text-sm space-y-2">
      <div className="flex items-center gap-2">
        {idx !== null && <span className="text-xs text-slate-500">window {idx}</span>}
        <StatusPill status="ok" />
        <span className="text-xs text-slate-400 font-mono">
          {w.classifier} · {w.mode} · threshold={fmt(w.threshold, 2)}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        <Stat label="events" value={String(w.n_train_events)} />
        <Stat label="class balance"
              value={fmtPct(w.train_class_balance, 1)} />
        <Stat label="train acc"
              value={fmtPct(w.train_accuracy, 1)} />
        <Stat label="precision@thr"
              value={fmtPct(w.train_precision_at_thresh, 1)} />
      </div>

      <div>
        <div className="text-xs text-slate-400 mb-1">feature importances</div>
        <div className="space-y-1">
          {importances.map(([name, val]) => (
            <div key={name} className="flex items-center gap-2 text-xs">
              <div className="font-mono w-32 truncate text-slate-300">{name}</div>
              <div className="flex-1 bg-bg border border-edge rounded h-3 relative">
                <div
                  className="bg-emerald-500/60 h-full rounded"
                  style={{ width: `${Math.max(2, (val / Math.max(maxImp, 1e-9)) * 100)}%` }}
                />
              </div>
              <div className="font-mono w-12 text-right text-slate-400">
                {fmt(val, 3)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg/30 border border-edge rounded p-1.5">
      <div className="text-slate-500">{label}</div>
      <div className="font-mono text-slate-200">{value}</div>
    </div>
  );
}

export function MetaLabelerCard({ strategy }: { strategy: string }) {
  const [payload, setPayload] = useState<StrategyMetaPayload | null | undefined>(
    undefined,
  );
  useEffect(() => {
    api.strategyMeta(strategy)
      .then((p) => setPayload(p))
      .catch(() => setPayload(null));
  }, [strategy]);

  if (payload === undefined) return null;          // still loading
  if (payload === null) return null;                // strategy doesn't use meta-labeling

  const m = payload.meta as any;
  const isMulti = m && typeof m === "object" && "per_window" in m;
  const windows: MetaWindowReport[] = isMulti ? m.per_window : [m];

  return (
    <div className="bg-panel border border-edge rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">Meta-labeler (iter {payload.iter})</h3>
        {isMulti && (
          <div className="text-xs text-slate-400">
            {m.n_windows} windows ·
            {m.all_ok ? " all ok" : m.any_skipped ? " some skipped" : " ok"}
            {m.mean_train_accuracy !== null && (
              <> · mean train acc {fmtPct(m.mean_train_accuracy, 1)}</>
            )}
          </div>
        )}
      </div>
      <div className="space-y-2">
        {windows.map((w, i) => (
          <WindowBlock key={i} w={w} idx={isMulti ? i : null} />
        ))}
      </div>
      <details className="text-xs text-slate-400 mt-3">
        <summary className="cursor-pointer text-slate-300">What is this?</summary>
        <p className="mt-2">
          A meta-labeler is a secondary classifier that decides{" "}
          <em>whether</em> (gate) or{" "}
          <em>how much</em> (scale) to act on the primary strategy's signal.
          It's trained on triple-barrier outcomes of the primary's own
          historical trades (López de Prado 2018). At decision time the
          primary signal is multiplied by, or gated by, the classifier's
          probability that the trade pays off.
        </p>
      </details>
    </div>
  );
}
