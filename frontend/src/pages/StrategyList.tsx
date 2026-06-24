import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type StrategySummary } from "../api";
import { fmt, fmtPct } from "../format";

function fmtStarted(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function StrategyList() {
  const [rows, setRows] = useState<StrategySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.strategies().then(setRows).catch((e) => setError(String(e)));
  }, []);

  return (
    <>
      <h1 className="text-2xl font-semibold mb-4">Strategies</h1>
      {error && <div className="text-rose-400 mb-4">{error}</div>}
      <div className="rounded-lg border border-edge bg-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-slate-400 text-xs uppercase tracking-wider">
            <tr>
              <th className="text-left px-4 py-2 font-medium">name</th>
              <th className="text-left px-4 py-2 font-medium">best composite</th>
              <th className="text-left px-4 py-2 font-medium">best pnl</th>
              <th className="text-left px-4 py-2 font-medium">best iter</th>
              <th className="text-left px-4 py-2 font-medium">iters</th>
              <th className="text-left px-4 py-2 font-medium">started</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows === null && (
              <tr>
                <td colSpan={7} className="px-4 py-3 text-slate-500">
                  loading…
                </td>
              </tr>
            )}
            {rows && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-3 text-slate-500">
                  no strategies in <code>strategies/</code>
                </td>
              </tr>
            )}
            {rows?.map((s) => (
              <tr key={s.name} className="border-t border-edge hover:bg-slate-800/40 align-top">
                <td className="px-4 py-2">
                  <Link to={`/strategies/${s.name}`} className="font-semibold text-blue-400">
                    {s.name}
                  </Link>
                  {s.description && (
                    <div className="text-xs text-slate-400 mt-1 max-w-xl leading-snug">
                      {s.description}
                    </div>
                  )}
                </td>
                <td className="px-4 py-2 mono">{fmt(s.best_composite)}</td>
                <td className="px-4 py-2 whitespace-nowrap">
                  <span
                    className={
                      s.best_pnl === null || s.best_pnl === undefined
                        ? "text-slate-500"
                        : s.best_pnl >= 0
                          ? "text-emerald-400 mono"
                          : "text-rose-400 mono"
                    }
                  >
                    {fmtPct(s.best_pnl)}
                  </span>
                  {s.best_pnl_iter !== null && s.best_pnl_iter !== undefined && (
                    <span className="ml-2 text-xs text-slate-500">iter {s.best_pnl_iter}</span>
                  )}
                </td>
                <td className="px-4 py-2">{s.best_iter ?? "—"}</td>
                <td className="px-4 py-2">{s.n_iters}</td>
                <td className="px-4 py-2 whitespace-nowrap text-slate-300">
                  {fmtStarted(s.first_started)}
                </td>
                <td className="px-4 py-2 text-right">
                  <Link to={`/strategies/${s.name}`} className="text-slate-400 hover:text-slate-100">
                    open →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
