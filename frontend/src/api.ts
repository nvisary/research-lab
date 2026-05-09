export type StrategySummary = {
  name: string;
  best_composite: number | null;
  best_iter: number | null;
  n_iters: number;
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

export type StrategyDetail = {
  name: string;
  best: Best;
  history: HistoryRow[];
  program_md: string;
  strategy_py: string;
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
    body: { start: string; end: string; tf: string; walk: number; note: string }
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
};
