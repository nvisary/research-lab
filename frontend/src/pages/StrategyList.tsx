import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type StrategySummary } from "../api";
import { fmt } from "../format";

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
              <th className="text-left px-4 py-2 font-medium">best iter</th>
              <th className="text-left px-4 py-2 font-medium">iters</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows === null && (
              <tr>
                <td colSpan={5} className="px-4 py-3 text-slate-500">
                  loading…
                </td>
              </tr>
            )}
            {rows && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-3 text-slate-500">
                  no strategies in <code>strategies/</code>
                </td>
              </tr>
            )}
            {rows?.map((s) => (
              <tr key={s.name} className="border-t border-edge hover:bg-slate-800/40">
                <td className="px-4 py-2">
                  <Link to={`/strategies/${s.name}`} className="font-semibold text-blue-400">
                    {s.name}
                  </Link>
                </td>
                <td className="px-4 py-2 mono">{fmt(s.best_composite)}</td>
                <td className="px-4 py-2">{s.best_iter ?? "—"}</td>
                <td className="px-4 py-2">{s.n_iters}</td>
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
