/**
 * Quality / problem-indicator panel.
 *
 * Reads the WF aggregate when available (worst_*  for pain indicators,
 * mean_*  for distribution properties), falls back to the OOS slice
 * metrics otherwise. Each row is colour-coded against an explicit
 * threshold; tooltip text comes from metricsHelp.ts.
 *
 * Thresholds are deliberately rough — they're decision aids, not
 * statistical assertions. Tune as we get more strategies.
 */
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Tooltip } from "./Tooltip";
import { api, type Metrics, type MonthlyReturnsPayload, type WfAggregate } from "../api";
import { fmt, fmtPct } from "../format";
import { helpFor } from "../metricsHelp";

type Props = {
  wf: WfAggregate;
  oos?: Metrics;
  strategy?: string;
  iter?: number | null;
};

type Verdict = "good" | "warn" | "bad" | "neutral";

const cls: Record<Verdict, string> = {
  good: "text-emerald-400",
  warn: "text-amber-400",
  bad: "text-rose-400",
  neutral: "text-slate-300",
};

function gradeMin(v: number | null | undefined, good: number, warn: number): Verdict {
  if (v === null || v === undefined) return "neutral";
  if (v >= good) return "good";
  if (v >= warn) return "warn";
  return "bad";
}
function gradeMax(v: number | null | undefined, good: number, warn: number): Verdict {
  if (v === null || v === undefined) return "neutral";
  if (v <= good) return "good";
  if (v <= warn) return "warn";
  return "bad";
}

function Row({
  label, value, verdict, hint,
}: { label: string; value: ReactNode; verdict: Verdict; hint?: string }) {
  return (
    <tr>
      <th className="text-left font-normal text-slate-400 align-top pr-4 py-1">
        {hint ? <Tooltip text={hint}><span>{label}</span></Tooltip> : label}
      </th>
      <td className={`${cls[verdict]} py-1`}>{value}</td>
    </tr>
  );
}

export function QualityIndicators({ wf, oos, strategy, iter }: Props) {
  // Fetch monthly returns to compute pct_positive_months on the FULL
  // stitched equity (matches the heatmap below). The backend's
  // wf_aggregate.mean_pct_positive_months averages per-OOS-window
  // calculations on slices ~1.3 months long with 1-2 monthly values
  // each — statistically unreliable. Derived from monthly_returns,
  // it's consistent with what the operator sees in the heatmap.
  const [monthly, setMonthly] = useState<MonthlyReturnsPayload | null>(null);
  useEffect(() => {
    if (!strategy || iter === null || iter === undefined) {
      setMonthly(null);
      return;
    }
    let cancelled = false;
    api.monthlyReturns(strategy, iter)
      .then((d) => { if (!cancelled) setMonthly(d); })
      .catch(() => { if (!cancelled) setMonthly(null); });
    return () => { cancelled = true; };
  }, [strategy, iter]);

  const pctPosFromStitched = useMemo<number | null>(() => {
    if (!monthly) return null;
    const flat = monthly.data.flat().filter((v): v is number => v !== null);
    if (flat.length === 0) return null;
    const positive = flat.filter((v) => v > 0).length;
    return (positive / flat.length) * 100;
  }, [monthly]);

  // Source-of-truth: WF aggregate when present (multi-window iter).
  // Fallback to OOS single-slice metrics for legacy iters.
  const sharpeGap     = wf?.worst_sharpe_gap ?? oos?.sharpe_gap ?? null;
  // % positive months: prefer stitched-equity calculation (honest) over
  // per-window mean (statistically noisy on short OOS slices).
  const pctPosMonths  = pctPosFromStitched
    ?? wf?.mean_pct_positive_months
    ?? oos?.pct_positive_months
    ?? null;
  const longestUWdays = wf?.worst_longest_underwater_days ?? oos?.longest_underwater_days ?? null;
  const pnlTop1       = wf?.worst_pnl_concentration_top1_pct ?? oos?.pnl_concentration_top1_pct ?? null;
  const pnlTop5       = wf?.worst_pnl_concentration_top5_pct ?? oos?.pnl_concentration_top5_pct ?? null;
  const tailRatio     = wf?.mean_tail_ratio ?? oos?.tail_ratio ?? null;
  const painIndex     = wf?.worst_pain_index ?? oos?.pain_index ?? null;
  const pctInPos      = wf?.mean_pct_time_in_position ?? oos?.pct_time_in_position ?? null;
  const avgDur        = wf?.mean_avg_trade_duration_hours ?? oos?.avg_trade_duration_hours ?? null;
  const skew          = wf?.mean_skew ?? oos?.skew ?? null;
  const kurt          = wf?.mean_kurt ?? oos?.kurt ?? null;

  // % time-in-position is a band: too low or too high are both bad.
  function gradeBand(v: number | null, lowBad: number, lowWarn: number,
                     highWarn: number, highBad: number): Verdict {
    if (v === null) return "neutral";
    if (v < lowBad || v > highBad) return "bad";
    if (v < lowWarn || v > highWarn) return "warn";
    return "good";
  }

  return (
    <table className="w-full text-sm">
      <tbody>
        <Row
          label="Sharpe gap (train→OOS)"
          hint={helpFor("Sharpe gap (train→OOS)")}
          verdict={gradeMax(sharpeGap, 0.5, 1.0)}
          value={
            <>
              <strong>{fmt(sharpeGap, 3)}</strong>
              {wf?.mean_sharpe_gap !== null && wf?.mean_sharpe_gap !== undefined && (
                <span className="text-xs text-slate-500 ml-2">
                  worst={fmt(sharpeGap, 2)} • mean={fmt(wf.mean_sharpe_gap, 2)}
                </span>
              )}
            </>
          }
        />
        <Row
          label="% positive months"
          hint={helpFor("% positive months")}
          verdict={gradeMin(pctPosMonths, 60, 50)}
          value={pctPosMonths === null ? "—" : `${pctPosMonths.toFixed(1)}%`}
        />
        <Row
          label="Longest underwater"
          hint={helpFor("Longest underwater")}
          verdict={gradeMax(longestUWdays, 60, 180)}
          value={longestUWdays === null ? "—" : `${longestUWdays.toFixed(0)} days`}
        />
        <Row
          label="PnL concentration top-1"
          hint={helpFor("PnL concentration top-1")}
          verdict={gradeMax(pnlTop1, 25, 50)}
          value={pnlTop1 === null ? "—" : `${pnlTop1.toFixed(1)}%`}
        />
        <Row
          label="PnL concentration top-5"
          hint={helpFor("PnL concentration top-5")}
          verdict={gradeMax(pnlTop5, 60, 90)}
          value={pnlTop5 === null ? "—" : `${pnlTop5.toFixed(1)}%`}
        />
        <Row
          label="Tail ratio"
          hint={helpFor("Tail ratio")}
          verdict={gradeMin(tailRatio, 1.0, 0.7)}
          value={tailRatio === null ? "—" : tailRatio.toFixed(2)}
        />
        <Row
          label="Pain index (Ulcer)"
          hint={helpFor("Pain index (Ulcer)")}
          verdict={gradeMax(painIndex, 0.05, 0.15)}
          value={painIndex === null ? "—" : fmtPct(painIndex)}
        />
        <Row
          label="% time in position"
          hint={helpFor("% time in position")}
          verdict={gradeBand(pctInPos, 20, 30, 80, 95)}
          value={pctInPos === null ? "—" : `${pctInPos.toFixed(1)}%`}
        />
        <Row
          label="Avg trade duration"
          hint={helpFor("Avg trade duration")}
          verdict="neutral"
          value={avgDur === null ? "—" : `${avgDur.toFixed(1)} h`}
        />
        <Row
          label="Skew"
          hint={helpFor("Skew")}
          verdict={gradeMin(skew, 0.0, -0.5)}
          value={skew === null ? "—" : skew.toFixed(2)}
        />
        <Row
          label="Kurt (excess)"
          hint={helpFor("Kurt")}
          verdict="neutral"
          value={kurt === null ? "—" : kurt.toFixed(2)}
        />
      </tbody>
    </table>
  );
}
