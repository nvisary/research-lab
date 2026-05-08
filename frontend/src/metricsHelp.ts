/**
 * Tooltip text for every metric / label shown in the dashboard.
 * Keep the explanations short, actionable, and consistent with what the
 * harness actually computes (see harness/metrics.py and harness/stats.py).
 */
export const HELP: Record<string, string> = {
  // ── Best card / iter-level ─────────────────────────────────────────────
  iter:
    "Iteration number. Increments on every runner.iterate call regardless of verdict.",
  composite:
    "Score that drives keep/revert. With walk-forward (default): mean(window_composites) − 0.5·std. " +
    "Per-window composite = OOS_Sharpe − 0.5·MaxDD − low-trades penalty. Higher is better. " +
    "n_trades=0 returns −∞ (ineligible).",
  DSR:
    "Deflated Sharpe Ratio in [0,1]. Probability of true edge given how many trials you've run. " +
    "PSR (single-sample correction for skew/kurt) deflated by the expected max-of-N under the null. " +
    "<0.5 = the best is most likely a noise-fit artifact; >0.95 = strong evidence of true edge.",
  PSR:
    "Probabilistic Sharpe Ratio in [0,1]. P(true Sharpe > benchmark | observed sample, skew, kurt). " +
    "Bailey & López de Prado 2012. Per-window value, not selection-adjusted (see DSR for that).",
  params:
    "Strategy parameters at this iteration (the contents of DEFAULT_PARAMS in strategy.py).",
  symbols:
    "Trading universe — strategy.py:DEFAULT_SYMBOLS.",
  period:
    "Train+val period the iteration ran over. Holdout (Oct 2025 → Apr 2026) is separate and not " +
    "visible to runner.iterate.",
  note:
    "The hypothesis the agent stated when running this iteration via --note. Future-you reads it.",

  // ── Walk-forward block ─────────────────────────────────────────────────
  "WF OOS sharpe":
    "Annualized OOS Sharpe across walk-forward windows: mean ± std, plus median for robustness. " +
    "Wide std means one lucky window is carrying the average — distrust it.",
  "WF max DD":
    "Maximum drawdown across WF windows. Worst = the most pessimistic window's MaxDD. Mean = typical.",
  "WF windows":
    "Number of walk-forward train/OOS windows the iterate period was split into (default 4).",
  "per-window composite":
    "Per-window composite scores. Wide spread = unstable strategy, narrow + positive = robust.",
  "WF CAGR":
    "Compound Annual Growth Rate per WF window, then averaged across windows. " +
    "Annualizes the OOS slice — answers 'if we ran this strategy with $10k for a year, what would it earn'. " +
    "Mean and median are reported because a single short OOS slice can give a noisy CAGR.",
  "WF return / window":
    "Mean total return inside each window's OOS slice (not annualized). " +
    "Each WF window is a self-contained backtest with its own $10k init, so this is the typical " +
    "return per window; for an annualized headline use WF CAGR.",
  "OOS CAGR":
    "Compound Annual Growth Rate on the OOS slice, annualized. Easier to read than Sharpe for " +
    "the layman: 'this strategy makes ~X% per year on average'.",
  "OOS total return":
    "Cumulative return over the OOS slice (not annualized).",

  // ── Single-split (no WF) view ──────────────────────────────────────────
  "train sharpe":
    "Annualized Sharpe on the train slice. A high train / low OOS gap (>1.0) is the classic " +
    "overfitting signature.",
  "OOS sharpe":
    "Annualized Sharpe on the out-of-sample slice. Used as the basis of the composite score.",
  "OOS max DD":
    "Largest peak-to-trough drawdown on OOS, as a positive fraction (0.10 == 10%).",
  "OOS trades":
    "Round-trip trade count on OOS. Below min_trades (50 by default) the composite gets penalized " +
    "by ~0.5·(1 − sqrt(n/50)).",

  // ── Holdout card ───────────────────────────────────────────────────────
  "for iter":
    "The iter number of the strategy.py snapshot evaluated on holdout. Matches best.json:iter " +
    "if you haven't edited strategy.py since the last KEEP.",
  "ran at":
    "When this holdout report was produced. Holdout is run manually by the user; the iteration " +
    "loop never touches it.",
  "train+val best":
    "Composite score on the train+val period (from runs/best.json) for comparison.",
  sharpe:
    "Annualized Sharpe on the slice (train, OOS, or holdout depending on context).",
  sortino:
    "Annualized Sortino — like Sharpe but only counts downside deviation in the denominator. " +
    "Sortino << Sharpe means the apparent edge is upside-tail luck.",
  calmar:
    "CAGR / |MaxDD|. Pain-relative-to-gain measure.",
  "max DD":
    "Largest peak-to-trough drawdown as a positive fraction.",
  trades:
    "Round-trip trade count on the slice.",

  // ── Trades card ────────────────────────────────────────────────────────
  "win rate":
    "Fraction of round-trip trades with PnL > 0.",
  "wins / losses":
    "Count of winning / losing trades.",
  "avg win":
    "Mean PnL of profitable trades, in quote currency (USDT).",
  "avg loss":
    "Mean PnL of losing trades. Negative number.",
  "payoff ratio":
    "avg_win / |avg_loss|. >1 means wins are larger than losses on average. " +
    "Combined with win rate gives expectancy: hit_rate × payoff − (1 − hit_rate).",
  "total PnL":
    "Sum of per-trade PnL across all trades, USDT, after fees and slippage.",
  "median duration":
    "Median holding period of trades, in hours.",

  // ── History table headers ──────────────────────────────────────────────
  verdict:
    "KEEP / BASELINE = new best, kept. REVERT = composite didn't beat best, file restored. " +
    "ERROR = strategy crashed during backtest. LOOKAHEAD_BUG = the audit caught a lookahead bug, " +
    "the file was reverted, and no backtest was run.",
  finished:
    "When this iteration completed (UTC).",
};

/** Resolve help text for a label, with a few common variants normalized. */
export function helpFor(label: string): string | undefined {
  if (HELP[label]) return HELP[label];
  // Be lenient with case for less-common labels:
  const lower = label.toLowerCase();
  for (const [k, v] of Object.entries(HELP)) {
    if (k.toLowerCase() === lower) return v;
  }
  return undefined;
}
