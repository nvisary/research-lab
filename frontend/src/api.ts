export type StrategySummary = {
  name: string;
  description: string | null;
  best_composite: number | null;
  best_iter: number | null;
  best_pnl: number | null;
  best_pnl_iter: number | null;
  n_iters: number;
  first_started: string | null;
};

export type Metrics = {
  sharpe?: number;
  bench_sharpe?: number | null;
  alpha_sharpe?: number | null;
  sortino?: number;
  calmar?: number;
  max_dd?: number;
  cagr?: number;
  total_return?: number;
  target_turnover?: number;
  /** @deprecated emitted under ``target_turnover`` post-tier-1; kept for old history rows. */
  turnover?: number;
  hit_rate?: number;
  n_trades?: number;
  n_periods?: number;
  psr?: number;
  sharpe_ci_lo?: number;
  sharpe_ci_hi?: number;
  // Quality / problem-detection (D1)
  sharpe_gap?: number | null;
  pct_positive_months?: number | null;
  longest_underwater_bars?: number | null;
  longest_underwater_days?: number | null;
  pnl_concentration_top1_pct?: number | null;
  pnl_concentration_top5_pct?: number | null;
  tail_ratio?: number | null;
  pain_index?: number | null;
  pct_time_in_position?: number | null;
  avg_trade_duration_hours?: number | null;
  median_trade_duration_hours?: number | null;
  skew?: number | null;
  kurt?: number | null;
};

export type BootstrapBlock = {
  n_boot: number;
  block_size: number;
  method: "block" | "permutation";
  observed: { sharpe: number; sortino: number; total_return: number; max_dd: number };
  p_values: { sharpe: number; sortino: number; total_return: number; max_dd: number };
  null_sharpe: {
    mean: number;
    std: number;
    quantiles_05_25_50_75_95: number[];
    hist_edges: number[];
    hist_counts: number[];
  };
  null_max_dd: { mean: number; quantiles_05_25_50_75_95: number[] };
};

export type HaircutSection = {
  p_value: number;
  t_stat: number;
  sharpe: number;
  haircut_pct: number;
};

export type HaircutSharpe = {
  n_trials: number;
  n_periods: number;
  raw: { sharpe: number; t_stat: number; p_value: number };
  bonferroni: HaircutSection;
  holm: HaircutSection;
  bhy: HaircutSection;
};

export type SessionOverfit = {
  n_iters: number;
  spearman_is_oos: number | null;
  slope_oos_on_is: number | null;
  intercept_oos: number | null;
  pct_is_top_half_below_oos_median: number | null;
  logit_overfit: number | null;
  is_median_sharpe?: number;
  oos_median_sharpe?: number;
  best_is_oos_gap: number | null;
  selection_inflation: number | null;
} | null;

export type ResearchStats = {
  bootstrap: { block: BootstrapBlock; permutation: BootstrapBlock | null };
  haircut_sharpe: HaircutSharpe;
  session_overfit: SessionOverfit;
  trial_sharpes: {
    n: number;
    min?: number;
    max?: number;
    mean?: number;
    median?: number;
    std?: number;
    expected_max_under_null?: number;
    selection_premium?: number;
    hist_edges?: number[];
    hist_counts?: number[];
  };
} | null;

export type HistoryRow = {
  iter: number;
  verdict: "KEEP" | "REVERT" | "BASELINE" | "ERROR" | string;
  composite: number | null;
  best_before: number | null;
  params: Record<string, unknown> | null;
  metrics_oos: Metrics;
  metrics_train: Metrics;
  wf_aggregate: WfAggregate;
  dsr: number | null;
  audit?: {
    audit?: string;
    error_type?: string;
    mode?: string;
    message?: string;
  } | null;
  research_stats?: ResearchStats;
  note: string;
  finished: string;
  error: string | null;
};

export type Best = {
  iter: number;
  composite: number;
  params: Record<string, unknown>;
  symbols: string[];
  tf: string;
  period: [string, string];
  metrics: { train: Metrics; oos: Metrics };
  wf_aggregate: WfAggregate;
  dsr: number | null;
  note: string;
  saved_at: string;
} | null;

export type TrustCheck = {
  name: string;
  passed: boolean | null;
  value: number | [number, number] | null;
  threshold: string;
  note: string;
};

export type TrustVerdict = {
  level: "green" | "yellow" | "red";
  label: string;
  checks: TrustCheck[];
  headline_sharpe: {
    raw: number | null;
    bhy: number | null;
    haircut_pct: number | null;
  };
  sign_agreement: {
    agree: number;
    total: number;
    train_signs: string[];
    oos_signs: string[];
  };
  stitched: {
    total_return: number;
    year_returns: { year: number; return: number }[];
    n_months: number;
    n_positive_months: number;
    pct_positive_months: number | null;
  } | null;
} | null;

export type StrategyDetail = {
  name: string;
  description: string | null;
  best: Best;
  history: HistoryRow[];
  program_md: string;
  strategy_py: string;
  trust_verdict: TrustVerdict;
};

export type EquityWindow = {
  window: number;
  timestamp: string[];
  equity: number[];
  benchmark: number[];
  split_cutoff: string | null;
};

export type EquityCurve = {
  iter: number;
  windows: EquityWindow[];
  n_windows: number;
  // legacy fields mirror the first window for back-compat
  timestamp: string[];
  equity: number[];
  benchmark: number[];
  split_cutoff: string | null;
};

export type WfAggregate = {
  mean_sharpe: number;
  std_sharpe: number;
  median_sharpe: number;
  mean_max_dd: number;
  worst_max_dd: number;
  mean_n_trades: number;
  mean_cagr?: number;
  median_cagr?: number;
  mean_total_return?: number;
  mean_bench_sharpe?: number | null;
  mean_alpha_sharpe?: number | null;
  median_alpha_sharpe?: number | null;
  window_alphas?: (number | null)[];
  n_windows: number;
  window_composites?: number[];
  // Quality aggregates (D1)
  mean_pct_positive_months?: number | null;
  worst_longest_underwater_bars?: number | null;
  worst_longest_underwater_days?: number | null;
  worst_pnl_concentration_top5_pct?: number | null;
  worst_pnl_concentration_top1_pct?: number | null;
  mean_tail_ratio?: number | null;
  worst_pain_index?: number | null;
  mean_pct_time_in_position?: number | null;
  mean_avg_trade_duration_hours?: number | null;
  mean_skew?: number | null;
  mean_kurt?: number | null;
  worst_sharpe_gap?: number | null;
  mean_sharpe_gap?: number | null;
} | null;

export type Job = {
  id: string;
  cmd: string[];
  status: "pending" | "running" | "done" | "failed";
  created_at: string;
  finished_at: string | null;
  exit_code: number | null;
  tail: string[];
};

const j = async <T>(p: string, init?: RequestInit): Promise<T> => {
  const r = await fetch(p, init);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${p}`);
  return r.json();
};

export type HoldoutReport = {
  report: {
    iter: number;
    ran_at: string;
    period: [string, string];
    tf: string;
    symbols: string[];
    params: Record<string, unknown>;
    metrics: Metrics;
    composite: number | null;
    best_composite_train_val: number | null;
  };
  equity: { timestamp: string[]; equity: number[]; benchmark: number[] } | null;
} | null;

export type TradeRow = {
  entry_time: string;
  exit_time: string;
  symbol: string;
  direction: "Long" | "Short" | string;
  size: number;
  entry_price: number;
  exit_price: number;
  pnl_quote: number;
  return_pct: number;
  duration_hours: number;
  slice: "train" | "oos" | string;
  window: number;
};

export type TradesSummary = {
  n_trades: number;
  n_wins?: number;
  n_losses?: number;
  win_rate?: number;
  avg_win?: number;
  avg_loss?: number;
  payoff_ratio?: number;
  total_pnl?: number;
  median_duration_hours?: number;
};

export type TradesPayload = {
  iter: number;
  summary: TradesSummary & {
    long?: TradesSummary;
    short?: TradesSummary;
  };
  rows: TradeRow[];
  row_count_total: number;
  top_winners: TradeRow[];
  top_losers: TradeRow[];
};

export type MonthlyReturnsPayload = {
  iter: number;
  years: number[];
  months: number[];
  data: (number | null)[][];     // rows = years, cols = months 1..12
  year_returns: (number | null)[];
  n_months: number;
};

export type PortfolioComponent = {
  strategy: string;
  capital: number;
  tf?: string | null;
};

export type PortfolioStrategyMeta = {
  name: string;
  best_iter: number | null;
  best_composite: number | null;
  tf: string | null;
  symbols: string[];
};

export type PortfolioReport = {
  ran_at: string;
  components: PortfolioComponent[];
  period: [string, string];
  embargo: string | null;
  lookback: string | null;
  cost_model: string;
  total_capital: number;
  final_equity: number;
  total_pnl_dollar: number;
  total_pnl_pct: number;
  combined_curve: {
    timestamp: string[];
    equity: number[];
    benchmark: number[];
  };
  per_strategy_curves: Record<string, {
    timestamp: string[];
    equity: number[];
    benchmark: number[];
  }>;
  per_strategy: Record<string, {
    capital: number;
    metrics: Record<string, any>;
    final_equity: number;
    pnl_dollar: number;
    pnl_pct: number;
  }>;
  portfolio_metrics: Record<string, any>;
  correlation_matrix: Record<string, Record<string, number | null>>;
};

export type OhlcvPayload = {
  symbol: string;
  tf: string;
  tf_requested?: string;
  start: string;
  end: string;
  n_bars: number;
  timestamp: string[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: number[];
};

export const api = {
  strategies: () => j<StrategySummary[]>("/api/strategies"),
  strategy: (name: string) => j<StrategyDetail>(`/api/strategies/${name}`),
  equity: (name: string, iter: number) =>
    j<EquityCurve>(`/api/strategies/${name}/equity/${iter}`),
  monthlyReturns: (name: string, iter: number) =>
    j<MonthlyReturnsPayload>(
      `/api/strategies/${name}/monthly-returns/${iter}`
    ),
  portfolioStrategies: () =>
    j<PortfolioStrategyMeta[]>("/api/portfolio/strategies"),
  portfolioRun: (body: {
    components: PortfolioComponent[];
    start: string;
    end: string;
    embargo?: string;
    lookback?: string;
    cost_model?: string;
  }) =>
    j<PortfolioReport>("/api/portfolio/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  ohlcv: (symbol: string, start: string, end: string, tf: string) =>
    j<OhlcvPayload>(
      `/api/data/ohlcv?symbol=${encodeURIComponent(symbol)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&tf=${encodeURIComponent(tf)}`
    ),
  iterate: (
    name: string,
    body: { start: string; end: string; tf: string; walk: number; note: string; expanding_wf?: boolean }
  ) =>
    j<Job>(`/api/strategies/${name}/iterate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  holdout: (name: string, body: { start: string; end: string; tf: string }) =>
    j<Job>(`/api/strategies/${name}/holdout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  holdoutReport: (name: string) =>
    j<HoldoutReport>(`/api/strategies/${name}/holdout`),
  trades: (name: string, iter: number) =>
    j<TradesPayload>(`/api/strategies/${name}/trades/${iter}`),
  job: (id: string) => j<Job>(`/api/jobs/${id}`),
  researchStats: (name: string) =>
    j<ResearchStatsPayload>(`/api/strategies/${name}/research-stats`),
  cpcvLatest: (name: string) =>
    j<CpcvLatestPayload>(`/api/strategies/${name}/cpcv`),
  cpcvList: (name: string) => j<CpcvListEntry[]>(`/api/strategies/${name}/cpcv/list`),
  cpcvRun: (name: string, body: {
    start: string; end: string; tf?: string | null;
    n_groups: number; k_test: number;
    embargo?: string | null; cost_model: string;
  }) =>
    j<Job>(`/api/strategies/${name}/cpcv`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  multistratLatest: () => j<MultiStratPayload | null>("/api/multistrat"),
  multistratList: () => j<MultiStratListEntry[]>("/api/multistrat/list"),
  multistratCandidates: () =>
    j<MultiStratCandidate[]>("/api/multistrat/candidates"),
  multistratRun: (body: {
    strategies?: string[] | null;
    n_boot?: number;
    block_size?: number | null;
    seed?: number | null;
    benchmark?: number;
    join?: "inner" | "outer";
  }) =>
    j<Job>("/api/multistrat/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  featuresList: () => j<FeatureMeta[]>("/api/features"),
  featurePreview: (name: string, symbol: string, start: string, end: string, tf: string) =>
    j<FeaturePreview>(
      `/api/features/${name}/preview?symbol=${encodeURIComponent(symbol)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&tf=${encodeURIComponent(tf)}`,
    ),
  featureCoverage: (name: string) =>
    j<FeatureCoverageRow[]>(`/api/features/${name}/coverage`),
  strategyMeta: (name: string) =>
    j<StrategyMetaPayload | null>(`/api/strategies/${name}/meta`),
  forwardLatest: (name: string) =>
    j<ForwardPayload | null>(`/api/strategies/${name}/forward`),
  forwardList: (name: string) =>
    j<ForwardListEntry[]>(`/api/strategies/${name}/forward/list`),
  forwardRun: (name: string, body: {
    start?: string | null; end?: string | null;
    tf?: string | null; lookback?: string | null;
  }) =>
    j<Job>(`/api/strategies/${name}/forward/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  forwardSummary: () => j<ForwardSummaryRow[]>("/api/forward/summary"),
  symbols: () => j<SymbolMeta[]>("/api/symbols"),
  sweepRun: (name: string, body: SweepRunRequest) =>
    j<Job>(`/api/strategies/${name}/sweep`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  sweepList: (name: string) =>
    j<SweepListEntry[]>(`/api/strategies/${name}/sweep/list`),
  sweepGet: (name: string, sweepId: string) =>
    j<SweepPayload>(`/api/strategies/${name}/sweep/${sweepId}`),
  sweepEquity: (name: string, sweepId: string, symbol: string, period: string) =>
    j<SweepEquityCurve>(
      `/api/strategies/${name}/sweep/${sweepId}/equity` +
        `?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(period)}`
    ),
  sweepCorrelations: (name: string, sweepId: string) =>
    j<SweepCorrelations | null>(
      `/api/strategies/${name}/sweep/${sweepId}/correlations`
    ),
};

// --------------------------------------------------------------------------- //
// Sweep (cross-symbol × cross-period robustness matrix)
// --------------------------------------------------------------------------- //
export type SymbolMeta = {
  symbol: string;
  n_months: number;
  first_month: string | null;
  last_month: string | null;
};

export type SweepRunRequest = {
  symbols?: string[];
  all_symbols?: boolean;
  all_symbols_covered?: boolean;
  top_n?: number;
  coverage_min?: number;
  periods?: string[];
  tf?: string;
  wf?: number;
  no_wf?: boolean;
  cost_model?: string;
  embargo?: string;
  lookback?: string;
  parallel?: number;
  tag?: string;
};

export type SweepCellRow = {
  symbol: string;
  period: string;
  period_start: string;
  period_end: string;
  sharpe: number | null;
  max_dd: number | null;
  n_trades: number | null;
  total_return: number | null;
  pct_time_in_position: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  information_ratio: number | null;
  cvar_95: number | null;
  max_participation_pct: number | null;
  train_sharpe: number | null;
  duration_s: number | null;
  equity_path: string | null;
  oos_returns_path: string | null;
  error: string | null;
};

export type SweepPeriodReport = {
  period: string;
  n_cells: number;
  n_cells_ok: number;
  n_errors: number;
  pct_sharpe_positive: number | null;
  pct_return_positive: number | null;
  median_sharpe: number | null;
  mean_sharpe: number | null;
  iqr_sharpe: number | null;
  median_max_dd: number | null;
  median_total_return: number | null;
  top: Array<{ symbol: string; sharpe: number; max_dd: number; total_return: number }>;
  bottom: Array<{ symbol: string; sharpe: number; max_dd: number; total_return: number }>;
};

export type SweepSymbolReport = {
  symbol: string;
  n_periods: number;
  pct_positive_periods: number | null;
  mean_sharpe: number | null;
  min_sharpe: number | null;
  max_sharpe: number | null;
  mean_total_return: number | null;
  worst_max_dd: number | null;
};

export type SweepReport = {
  per_period: SweepPeriodReport[];
  per_symbol: SweepSymbolReport[];
  global: {
    n_cells: number;
    n_errors: number;
    median_sharpe: number | null;
    mean_sharpe: number | null;
    pct_sharpe_positive: number | null;
  };
};

export type SweepManifest = {
  sweep_id: string;
  strategy: string;
  strategy_sha256: string;
  created_at: string;
  finished_at: string | null;
  tag: string;
  tf: string;
  walk_windows: number;
  no_wf: boolean;
  cost_model: string;
  embargo: string;
  lookback: string;
  symbols: string[];
  periods: Array<{ label: string; start: string; end: string }>;
  coverage_min: number;
  selection_mode: string;
  n_cells?: number;
  n_errors?: number;
  duration_s?: number;
};

export type SweepProgress = {
  done: number;
  total: number;
  elapsed_s: number;
};

export type SweepPayload = {
  manifest: SweepManifest;
  report: SweepReport | null;
  summary: SweepCellRow[];
  progress: SweepProgress | null;
};

export type SweepListEntry = {
  sweep_id: string;
  created_at: string;
  finished_at: string | null;
  tag: string;
  tf: string;
  n_symbols: number;
  n_periods: number;
  n_cells: number | null;
  n_errors: number | null;
  duration_s: number | null;
  selection_mode: string;
  cost_model: string;
  strategy_sha256: string;
  global: SweepReport["global"] | null;
  progress: SweepProgress | null;
};

export type SweepEquityCurve = {
  timestamp: string[];
  equity: number[];
  benchmark: (number | null)[];
  window?: number[];
};

export type SweepCorrelations = {
  symbols: string[];
  matrix: (number | null)[][];
};

// --------------------------------------------------------------------------- //
// Forward-test (post-holdout drift detection)
// --------------------------------------------------------------------------- //
export type ForwardDrift = {
  n_periods: number;
  forward_sharpe: number;
  forward_total_return: number;
  forward_max_dd: number;
  forward_volatility: number;
  backtest_sharpe: number | null;
  backtest_sharpe_ci_lo: number | null;
  backtest_sharpe_ci_hi: number | null;
  sharpe_z_vs_backtest: number | null;
  in_ci: boolean | null;
  consecutive_below_ci_days: number;
  forward_psr: number | null;
  flag: "ok" | "warn" | "alert" | "unknown";
  flag_reason: string;
};

export type ForwardReport = {
  iter: number;
  ran_at: string;
  period: [string, string];
  tf: string;
  symbols: string[];
  code_source: string;
  snapshot_used: boolean;
  metrics: Record<string, any>;
  drift: ForwardDrift;
  backtest_oos_sharpe: number | null;
  backtest_sharpe_ci_lo: number | null;
  backtest_sharpe_ci_hi: number | null;
};

export type ForwardPayload = {
  report: ForwardReport;
  equity: {
    timestamp: string[];
    equity: number[];
    benchmark: number[];
    rolling_sharpe_30d?: (number | null)[];
  } | null;
  report_file: string;
};

export type ForwardListEntry = {
  file: string;
  ran_at: string;
  period: [string, string];
  iter: number;
  snapshot_used: boolean;
  forward_sharpe: number | null;
  forward_max_dd: number | null;
  forward_psr: number | null;
  flag: string | null;
};

export type ForwardSummaryRow = {
  name: string;
  has_forward: boolean;
  flag: "ok" | "warn" | "alert" | "unknown" | null;
  ran_at: string | null;
  forward_sharpe: number | null;
  backtest_sharpe: number | null;
  backtest_sharpe_ci_lo: number | null;
  backtest_sharpe_ci_hi: number | null;
  consecutive_below_ci_days?: number;
  period?: [string, string];
};

// --------------------------------------------------------------------------- //
// Features + meta-labeler
// --------------------------------------------------------------------------- //
export type FeatureMeta = {
  name: string;
  description: string;
  deps: string[];
  lookback: string;
  source_file: string;
  source_line: number | null;
};

export type FeaturePreview = {
  name: string;
  symbol: string;
  tf: string;
  start: string;
  end: string;
  timestamp: string[];
  values: (number | null)[];
  n_points: number;
  quantiles_05_25_50_75_95: (number | null)[];
};

export type FeatureCoverageRow = {
  symbol: string;
  tf: string;
  months: string[];
  n_months: number;
  first: string;
  last: string;
};

export type MetaWindowReport = {
  status: "ok" | "skipped" | "failed";
  reason?: string;
  error?: string;
  classifier?: "logreg" | "gbm";
  mode?: "scale" | "gate";
  threshold?: number;
  features?: string[];
  n_train_events?: number;
  n_train_positive?: number;
  train_class_balance?: number;
  train_accuracy?: number;
  train_precision_at_thresh?: number;
  train_recall_at_thresh?: number;
  feature_importances?: Record<string, number>;
};

export type StrategyMetaPayload = {
  iter: number;
  meta:
    | MetaWindowReport
    | {
        per_window: MetaWindowReport[];
        n_windows: number;
        all_ok: boolean;
        mean_train_accuracy: number | null;
        any_skipped: boolean;
      };
};

// --------------------------------------------------------------------------- //
// MultiStrat (Reality Check / SPA / Romano-Wolf)
// --------------------------------------------------------------------------- //
export type MultiStratCandidate = {
  name: string;
  has_best: boolean;
  best_iter: number | null;
  composite: number | null;
  tf?: string;
  equity_present: boolean;
};

export type MultiStratListEntry = {
  file: string;
  ran_at: string;
  n_strategies_used: number;
  n_days: number;
  reality_check_p: number | null;
  spa_p_consistent: number | null;
  n_reject_at_05: number;
};

export type MultiStratPerStrategy = {
  strategy: string;
  n_periods: number;
  mean: number;
  std: number;
  sharpe_per_period: number;
  rw_p_adj: number;
  rw_rank: number;
};

export type MultiStratRealityCheck = {
  test_stat: number;
  p_value: number;
  null_quantiles_05_50_95: number[];
  n_boot: number;
};

export type MultiStratSPA = {
  test_stat_studentized: number;
  p_value_lower: number;
  p_value_consistent: number;
  p_value_upper: number;
  n_kept_consistent: number;
  n_kept_upper: number;
  threshold_consistent: number;
  n_boot: number;
};

export type MultiStratRWRow = {
  strategy: string;
  obs_stat_sqrtT_mean: number;
  rank: number;
  p_adj: number;
  reject_at_05: boolean;
  reject_at_10: boolean;
};

export type MultiStratReport = {
  ran_at: string;
  n_strategies_input: number;
  n_strategies_used: number;
  n_days: number;
  strategies_used: string[];
  strategies_skipped: Array<{ strategy: string; reason?: string }>;
  per_strategy_meta: Record<string, {
    iter?: number;
    n_days?: number;
    composite?: number;
    tf?: string;
    oos_start?: string;
    oos_end?: string;
  }>;
  join: string;
  seed: number | null;
  correlation_matrix: Record<string, Record<string, number | null>>;
  tests: {
    n_strategies: number;
    n_periods: number;
    block_size: number;
    n_boot: number;
    benchmark: number;
    period_start: string;
    period_end: string;
    per_strategy: MultiStratPerStrategy[];
    reality_check: MultiStratRealityCheck;
    spa: MultiStratSPA;
    romano_wolf: MultiStratRWRow[];
  };
};

export type MultiStratPayload = {
  report: MultiStratReport;
  daily_returns: {
    timestamp: string[];
    returns: Record<string, number[]>;
    equity_curves: Record<string, number[]>;
  } | null;
  report_file: string;
};

export type ResearchStatsPayload = {
  history_present: boolean;
  n_iters?: number;
  trial_sharpes?: {
    n: number;
    min?: number; max?: number; mean?: number; median?: number; std?: number;
    expected_max_under_null?: number; selection_premium?: number;
    hist_edges?: number[]; hist_counts?: number[];
  };
  session_overfit?: SessionOverfit;
  latest?: { iter: number | null; research_stats: ResearchStats };
  per_iter?: Array<{
    iter: number;
    verdict: string;
    composite: number | null;
    oos_sharpe: number | null;
    dsr: number | null;
    p_sharpe_block: number | null;
    p_max_dd_block: number | null;
    bhy_sharpe: number | null;
    bhy_haircut_pct: number | null;
  }>;
};

export type CpcvOverfit = {
  n_paths: number;
  spearman_is_oos: number | null;
  slope_oos_on_is: number | null;
  intercept_oos: number | null;
  pct_is_top_half_below_oos_median: number | null;
  logit_overfit: number | null;
  is_median_sharpe?: number;
  oos_median_sharpe?: number;
};

export type CpcvLatestPayload = {
  report: {
    iter: number;
    ran_at: string;
    period: [string, string];
    tf: string;
    symbols: string[];
    n_groups: number;
    k_test: number;
    n_paths: number;
    embargo: string;
    cost_model: string;
    summary: Record<string, any>;
    overfit: CpcvOverfit;
    overfit_verdict: string;
    best_composite_train_val: number | null;
  };
  paths: Array<{
    test_groups: string;
    n_periods: number;
    n_periods_is: number;
    n_trades: number;
    sharpe: number;
    is_sharpe: number;
    sortino: number;
    max_dd: number;
    total_return: number;
    hit_rate: number;
  }> | null;
  report_file: string;
} | null;

export type CpcvListEntry = {
  file: string;
  iter: number;
  ran_at: string;
  n_paths: number;
  n_groups: number;
  k_test: number;
  median_sharpe: number | null;
  overfit_verdict: string | null;
  spearman_is_oos: number | null;
};
