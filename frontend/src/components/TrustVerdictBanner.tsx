import type { TrustVerdict } from "../api";
import { fmt, fmtPct } from "../format";

type Props = { verdict: TrustVerdict };

const LEVEL_STYLES: Record<"green" | "yellow" | "red", {
  bg: string; border: string; chip: string; icon: string; chipLabel: string;
}> = {
  green: {
    bg: "bg-emerald-900/30",
    border: "border-emerald-500/60",
    chip: "bg-emerald-500 text-emerald-950",
    icon: "✓",
    chipLabel: "TRUST",
  },
  yellow: {
    bg: "bg-amber-900/30",
    border: "border-amber-500/60",
    chip: "bg-amber-500 text-amber-950",
    icon: "⚠",
    chipLabel: "INVESTIGATE",
  },
  red: {
    bg: "bg-rose-900/30",
    border: "border-rose-500/60",
    chip: "bg-rose-500 text-rose-950",
    icon: "✗",
    chipLabel: "DO NOT TRUST",
  },
};

function CheckRow({ name, passed, value, threshold, note }: {
  name: string;
  passed: boolean | null;
  value: number | [number, number] | null;
  threshold: string;
  note: string;
}) {
  let icon: string;
  let iconCls: string;
  if (passed === true)       { icon = "✓"; iconCls = "text-emerald-400"; }
  else if (passed === false) { icon = "✗"; iconCls = "text-rose-400"; }
  else                       { icon = "?"; iconCls = "text-slate-500"; }

  let display: string;
  if (value === null || value === undefined) display = "—";
  else if (Array.isArray(value)) display = `${value[0]}/${value[1]}`;
  else if (Math.abs(value) >= 1) display = value.toFixed(3);
  else display = value.toFixed(3);

  return (
    <div className="flex items-start gap-3 py-1.5 border-t border-edge/60 first:border-t-0">
      <span className={`${iconCls} text-lg font-bold w-5 flex-shrink-0`}>{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="font-medium text-slate-200">{name}</span>
          <span className="mono text-slate-300">{display}</span>
          <span className="text-xs text-slate-500">threshold: {threshold}</span>
        </div>
        <div className="text-xs text-slate-400 mt-0.5">{note}</div>
      </div>
    </div>
  );
}

export function TrustVerdictBanner({ verdict }: Props) {
  if (!verdict) return null;
  const s = LEVEL_STYLES[verdict.level];

  const hl = verdict.headline_sharpe;
  const sa = verdict.sign_agreement;
  const st = verdict.stitched;

  return (
    <section className={`rounded-lg border-2 ${s.border} ${s.bg} p-4 mb-4`}>
      <div className="flex items-start gap-4">
        <span className={`${s.chip} px-3 py-1.5 rounded font-bold tracking-wider text-sm flex-shrink-0`}>
          {s.icon} {s.chipLabel}
        </span>
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-semibold text-slate-100 leading-tight">
            {verdict.label}
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Four independent statistical checks. Green requires all passed; red triggers on ≥ 2 failures.
            Composite alone optimizes OOS Sharpe — these checks ask if that Sharpe represents real
            edge or selection-bias artefact.
          </p>
        </div>
      </div>

      {/* Headline stats — what to read instead of (or alongside) raw composite */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
        <Stat
          label="Raw Sharpe"
          value={fmt(hl.raw)}
          help="Best iter's stitched OOS Sharpe, BEFORE multiple-testing correction."
        />
        <Stat
          label="BHY-haircut Sharpe"
          value={fmt(hl.bhy)}
          highlight={hl.bhy !== null && hl.bhy > 0.5}
          warn={hl.bhy !== null && hl.bhy <= 0.5}
          help="Raw Sharpe MINUS the selection-bias tax (Harvey-Liu-Zhu). This is the honest Sharpe."
        />
        <Stat
          label="Train↔OOS agree"
          value={sa.total ? `${sa.agree}/${sa.total}` : "—"}
          highlight={sa.total > 0 && sa.agree / sa.total >= 0.75}
          warn={sa.total > 0 && sa.agree / sa.total < 0.75}
          help="Walk-forward windows where train Sharpe and OOS Sharpe agree on sign. Inversion = selection-on-OOS pattern."
        />
        <Stat
          label="Stitched 24mo P&L"
          value={st ? fmtPct(st.total_return) : "—"}
          highlight={st !== null && st.total_return >= 0}
          warn={st !== null && st.total_return < 0}
          help="Full-period account if you ran the strategy non-stop. Composite uses OOS slices only (~25% of time)."
        />
      </div>

      {/* Train↔OOS sign-agreement mini-table */}
      {sa.total > 0 && (
        <div className="mt-4 text-xs">
          <div className="text-slate-400 mb-1">per-window train vs OOS sign:</div>
          <div className="inline-block border border-edge rounded overflow-hidden">
            <table className="mono">
              <tbody>
                <tr className="border-b border-edge">
                  <td className="px-2 py-1 text-slate-500 bg-slate-900">train</td>
                  {sa.train_signs.map((s, i) => (
                    <td key={i} className={`px-2 py-1 text-center font-semibold
                        ${s === "+" ? "text-emerald-400" : s === "-" ? "text-rose-400" : "text-slate-500"}`}>
                      {s}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="px-2 py-1 text-slate-500 bg-slate-900">OOS</td>
                  {sa.oos_signs.map((s, i) => {
                    const match = s === sa.train_signs[i];
                    return (
                      <td key={i} className={`px-2 py-1 text-center font-semibold
                          ${match ? "" : "bg-rose-950/40"}
                          ${s === "+" ? "text-emerald-400" : s === "-" ? "text-rose-400" : "text-slate-500"}`}>
                        {s}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Year-by-year stitched returns */}
      {st && st.year_returns.length > 0 && (
        <div className="mt-3 text-xs">
          <span className="text-slate-400">Per-year compounded (stitched): </span>
          {st.year_returns.map((yr, i) => (
            <span key={yr.year} className="mono ml-2">
              <span className="text-slate-500">{yr.year}:</span>{" "}
              <span className={yr.return >= 0 ? "text-emerald-400" : "text-rose-400"}>
                {fmtPct(yr.return)}
              </span>
              {i < st.year_returns.length - 1 && <span className="text-slate-700"> ·</span>}
            </span>
          ))}
          {st.pct_positive_months !== null && (
            <span className="ml-3 text-slate-500">
              · positive months: {(st.pct_positive_months * 100).toFixed(0)}%
              <span className="text-slate-700"> ({st.n_positive_months}/{st.n_months})</span>
            </span>
          )}
        </div>
      )}

      {/* Detailed check rows */}
      <details className="mt-4 group">
        <summary className="cursor-pointer text-xs text-slate-400 hover:text-slate-200 select-none">
          ▸ four independent checks (click to expand)
        </summary>
        <div className="mt-2 bg-slate-950/50 rounded p-3 border border-edge/40">
          {verdict.checks.map((c) => (
            <CheckRow key={c.name} {...c} />
          ))}
        </div>
      </details>
    </section>
  );
}

function Stat({ label, value, help, highlight, warn }: {
  label: string; value: React.ReactNode; help: string;
  highlight?: boolean; warn?: boolean;
}) {
  const valueCls = warn ? "text-rose-400"
                  : highlight ? "text-emerald-400"
                  : "text-slate-200";
  return (
    <div className="bg-slate-950/50 border border-edge/40 rounded p-2"
         title={help}>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mono text-lg font-semibold ${valueCls}`}>{value}</div>
    </div>
  );
}
