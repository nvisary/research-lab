/**
 * Forward page — cross-strategy drift dashboard.
 *
 * Shows each strategy's latest forward-test verdict at a glance:
 * drift flag, forward Sharpe vs backtest CI, last-run timestamp.
 * Click a row to jump to the strategy's detail page where the full
 * ForwardCard lives.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ForwardSummaryRow } from "../api";
import { fmt } from "../format";

function FlagPill({ flag }: { flag: ForwardSummaryRow["flag"] }) {
  const map: Record<string, string> = {
    ok: "bg-emerald-500/15 text-emerald-300",
    warn: "bg-amber-500/15 text-amber-300",
    alert: "bg-rose-500/15 text-rose-300",
    unknown: "bg-slate-500/15 text-slate-400",
  };
  const key = flag ?? "unknown";
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono uppercase ${map[key]}`}>
      {key}
    </span>
  );
}

function MiniBar({
  lo, hi, fwd,
}: {
  lo: number | null; hi: number | null; fwd: number | null;
}) {
  if (lo === null || hi === null || fwd === null) {
    return <span className="text-slate-500 text-xs">—</span>;
  }
  const min = Math.min(lo, fwd, -0.5);
  const max = Math.max(hi, fwd, 0.5);
  const pct = (x: number) => ((x - min) / (max - min)) * 100;
  return (
    <div className="relative h-3 w-40 bg-bg border border-edge rounded">
      <div className="absolute top-0 bottom-0 bg-emerald-500/20"
            style={{ left: `${pct(lo)}%`, width: `${pct(hi) - pct(lo)}%` }} />
      <div className="absolute top-0 bottom-0 w-px bg-amber-300"
            style={{ left: `${pct(fwd)}%` }} />
      <div className="absolute top-0 bottom-0 w-px bg-slate-500/50"
            style={{ left: `${pct(0)}%` }} />
    </div>
  );
}

export function Forward() {
  const [rows, setRows] = useState<ForwardSummaryRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.forwardSummary().then(setRows).catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded p-3 text-sm">
        {error}
      </div>
    );
  }

  const withForward = rows.filter((r) => r.has_forward);
  const without = rows.filter((r) => !r.has_forward);

  return (
    <div className="space-y-4">
      <div className="bg-panel border border-edge rounded-lg p-4">
        <h2 className="font-semibold mb-1">Forward-test dashboard</h2>
        <p className="text-xs text-slate-400 mb-3 max-w-3xl">
          Each strategy's locked best snapshot run on bars AFTER the holdout
          window ended. Drift flag compares realised forward Sharpe to the
          backtest's OOS Sharpe CI. Click a strategy to run / inspect its
          full forward report.
        </p>

        {withForward.length === 0 ? (
          <div className="text-sm text-slate-500">
            No forward reports yet. Pick a strategy → ForwardCard → press
            <b> run forward-test</b>.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-slate-400 text-xs">
                <tr className="border-b border-edge">
                  <th className="text-left px-2 py-1">strategy</th>
                  <th className="text-center px-2 py-1">flag</th>
                  <th className="text-right px-2 py-1">backtest CI lo</th>
                  <th className="text-right px-2 py-1">backtest CI hi</th>
                  <th className="text-right px-2 py-1">forward sharpe</th>
                  <th className="text-left px-2 py-1">CI bar</th>
                  <th className="text-right px-2 py-1">below CI</th>
                  <th className="text-left px-2 py-1">last run</th>
                </tr>
              </thead>
              <tbody>
                {withForward.map((r) => (
                  <tr key={r.name} className="border-b border-edge/40 hover:bg-bg/30">
                    <td className="px-2 py-1">
                      <Link
                        to={`/strategies/${r.name}`}
                        className="font-mono text-slate-200 hover:text-emerald-300"
                      >
                        {r.name}
                      </Link>
                    </td>
                    <td className="px-2 py-1 text-center">
                      <FlagPill flag={r.flag} />
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-slate-400">
                      {fmt(r.backtest_sharpe_ci_lo, 2)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-slate-400">
                      {fmt(r.backtest_sharpe_ci_hi, 2)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {fmt(r.forward_sharpe, 3)}
                    </td>
                    <td className="px-2 py-1">
                      <MiniBar
                        lo={r.backtest_sharpe_ci_lo}
                        hi={r.backtest_sharpe_ci_hi}
                        fwd={r.forward_sharpe}
                      />
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {r.consecutive_below_ci_days
                        ? `${r.consecutive_below_ci_days}d`
                        : "—"}
                    </td>
                    <td className="px-2 py-1 text-xs text-slate-500 font-mono">
                      {r.ran_at?.slice(0, 19).replace("T", " ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {without.length > 0 && (
        <div className="bg-panel border border-edge rounded-lg p-4">
          <h3 className="font-semibold mb-2">Awaiting forward-test</h3>
          <div className="flex flex-wrap gap-2">
            {without.map((r) => (
              <Link
                key={r.name}
                to={`/strategies/${r.name}`}
                className="font-mono text-xs px-2 py-1 bg-bg/40 border border-edge rounded text-slate-400 hover:text-emerald-300 hover:border-emerald-500/40"
              >
                {r.name}
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
