# METHODS.md — strategy improvement playbook

A catalog of techniques an agent can deploy to improve a strategy. This is **not** a checklist to run end-to-end. It is a vocabulary. Pick **one** technique per iteration, articulate the hypothesis it embodies, and let the harness judge.

Before applying anything here, read `AGENTS.md` — especially the no-lookahead, no-holdout-peek, one-change-per-iter rules. The methods below are *only* useful inside that discipline.

> Convention used throughout: `params: dict` keys are added to `DEFAULT_PARAMS` and surfaced in `PARAM_SPACE` so future iterations can tune them.

---

## 1. Signal generation — the *what*

### 1.1 Indicator family swaps
The cheapest experiment: replace one indicator with a sibling.

| From | To | Hypothesis |
|---|---|---|
| EMA / SMA | WMA, HMA, Kaufman AMA | reduce lag without overshooting |
| RSI | Stochastic RSI, Connors RSI | sharper turn detection |
| MACD | PPO (percentage price oscillator) | scale-invariant, comparable across symbols |
| Bollinger | Keltner channels (ATR-based) | adapts to vol better than σ-based bands |
| Donchian breakout | Volatility-adjusted breakout (`high - close > k·atr`) | filters fake breakouts |

### 1.2 Multi-timeframe confirmation
Cheapest robustness win after indicator swaps. Trigger on a fast TF, gate by
one or more slower TFs. Set `DEFAULT_TF` to the FAST one (the decision
frequency); derive slower ones inside `generate_signals` via the helper:

```python
from harness.utils import resample_higher

DEFAULT_TF = "5min"

def generate_signals(data, params):
    df = data["BTCUSDT"]
    df_4h = resample_higher(df, "4h", {"close": "last"}, target_index=df.index)
    trend_up = df_4h["close"] > df_4h["close"].ewm(span=200).mean()
    pos = pos.where(trend_up, 0.0)   # only longs when 4h trend is up
```

The helper applies `.shift(1)` on the resampled series so the value you read at
decision time `t` comes from the previous COMPLETED higher-TF bar — no
lookahead. Plain `df.resample("4h").agg({"close": "last"})` is a **forward-look
bug** (the 08:00 bar's `close` equals `df.close[11:00]`); the audit will catch
it but you save iterations by using the helper from the start.

Hypothesis: most chop-period whipsaws happen against the higher-TF trend.
Gating cuts them at low cost. Triple confirmation (5m × 30m × 4h) trades less
but with much higher selectivity.

### 1.3 Momentum families
- **Time-series momentum**: `sign(return_n)` over a lookback. Carries trend exposure.
- **Cross-sectional momentum**: rank top-N vs bottom-N across symbols by return; long top, short bottom. Hedges market beta.
- **Breakout momentum**: `close > rolling(close, n).max().shift(1)`. Crisp entry, requires costs accounting.
- **Acceleration**: `Δreturn_n` rather than `return_n`. Picks up regime *changes*, not levels.

### 1.4 Mean-reversion families
- **Z-score reversion**: `z = (close - rolling_mean) / rolling_std`; enter at `|z| > k`, exit at `|z| < 0.5`.
- **Pair / basket reversion**: trade the spread between two correlated symbols. Needs cointegration check (Engle-Granger or Johansen) over the *train* slice only.
- **Liquidation reversion**: after a 1m candle with extreme range (`(high-low)/atr > k`), fade the close-vs-vwap. Microstructure-style.

### 1.5 Volatility / range expansion
- Trade only when `realized_vol_n` is in some quantile band of its distribution.
- Trade only when `ATR / close` is increasing (regime expansion) or decreasing (regime compression).
- Use realized-vs-implied vol if you have options/funding context.

---

## 2. Filters — the *when not to trade*

The single biggest source of free improvement: stop trading in regimes where your edge is absent.

### 2.1 Trend filters
- 200-period MA slope: `close > ma_200` for longs only, `close < ma_200` for shorts only.
- ADX > 20 (trending market) — gates mean-reversion *off*.
- ADX < 20 (ranging) — gates trend-following *off*.

### 2.2 Volatility filters
- Skip when `atr / close < q10` (no movement → no edge to capture).
- Skip when `atr / close > q95` (extreme moves → costs/slippage murder you).
- Skip when realized vol diverges sharply from its 30d average (vol spike).

### 2.3 Time-of-day / day-of-week filters
**Use with caution** — easy to overfit. Justifiable only if the mechanism is real:
- Weekend BTC liquidity is thin → wider spreads → cut size or skip.
- Around CME futures open (Sun 23:00 UTC) → BTC tends to gap.
- Near scheduled events (FOMC, CPI prints) you treat as "no-trade windows".

If you add such a filter, articulate the *mechanism*, not just the calendar pattern.

### 2.4 Funding-rate filters
Bybit perp pays funding every 8h. Sign and magnitude affect carry:
- Long when funding is negative (you get paid to hold) and trend is up.
- Avoid shorts when funding is deeply negative — basket of shorts pays compounded carry.
- Filter out positions whose expected edge is smaller than the next 8h funding payment.

### 2.5 Sentiment / regime indicators
- BTC dominance trend up → alts underperform → reduce alt exposure.
- Open-interest spike + price flat → squeeze setup → mean-revert bias.
- Liquidation cascades visible in volume / range → fade the next bar.

---

## 3. Position sizing — the *how much*

The harness clips `position` to `[-1, 1]`. Within that, you have full freedom.

### 3.1 Scalar conviction
Replace ±1 with a continuous strength signal:
```python
strength = (ema_fast - ema_slow) / atr
pos = strength.clip(-1, 1)
```
Hypothesis: strong signals deserve full size, weak ones should be partial.

### 3.2 Volatility targeting
Equalize *risk per trade* rather than notional:
```python
target_vol = 0.02  # 2% daily target
realized_vol = close.pct_change().rolling(30).std() * sqrt(periods_per_day)
size = (target_vol / realized_vol).clip(upper=1.0)
pos = direction * size
```
Why: in equal-notional, a high-vol asset dominates your DD. Vol-targeting flattens contribution.

### 3.3 Kelly-style fractional sizing
With a calibrated edge `μ` and variance `σ²`, optimal Kelly fraction is `μ/σ²`. Use **fractional Kelly** (e.g. `0.25·Kelly`) — full Kelly assumes you know the distribution exactly, which you don't.

### 3.4 Drawdown control
Reduce size after a string of losses to survive the bad streak:
```python
recent_pnl = (returns.rolling(20).sum())   # CAREFUL: use lagged returns to avoid lookahead
scale = 1.0 - 0.5 * (recent_pnl < -0.05)   # halve size after >5% rolling-20 loss
```
Hypothesis: vol clusters → losing streaks cluster → reducing exposure during them improves Sortino.

### 3.5 Regime-conditional sizing
Different regimes deserve different sizes. Define regime classifier (e.g. `vol_quantile × trend_sign`) and pre-train a size multiplier per bucket on *train only*.

---

## 4. Risk management — the *when to cut*

### 4.1 Stop-loss
- **Fixed-percent**: simplest, easy to overfit ("a 2.7% stop fits the worst trade nicely!").
- **ATR-based**: `stop = entry - k·atr`. Adapts to volatility — much harder to overfit because `k` is unitless.
- **Volatility-targeted**: stop tightens as realized vol rises.
- **Time-based**: exit if no progress after N bars. Captures "thesis decayed" failure mode.

When adding a stop, *only* tune `k` (the multiplier), never the absolute level. Absolute levels are calendar-overfits in disguise.

### 4.2 Take-profit
Generally weakens an edge (cuts winners). Use only if your strategy has measurable mean-reversion in profits, e.g. funding-driven scalps. Default: don't add a TP; let trends run, exit on signal flip.

### 4.3 Trailing stop
Locks in gains as price moves with you. Most useful for trend-followers with skewed payoff distributions. Two flavors:
- Chandelier: `trail = high_since_entry - k·atr`.
- Percentage: `trail = high · (1 - p)`.

### 4.4 Position-level circuit breakers
At the *portfolio* level (not strategy):
- Equity at-risk cap: if open-position drawdown > 5%, halve all positions.
- Daily-loss kill switch: flatten everything once intraday PnL < -X%.

Implement these only at the very end, on a strategy that already shows edge. Premature risk capping kills the alpha you're trying to detect.

---

## 5. Multi-asset & cross-sectional

### 5.1 Multi-symbol diversification
Add symbols to `DEFAULT_SYMBOLS`. The harness equal-weights them. **Do not** cherry-pick the symbols where the current best looks good — you're fitting the universe.

> **Survivorship-bias caveat.** The data layer only has currently-listed
> Bybit perps. Adding alts to the universe means the backtest is
> *survival-conditioned* — symbols that delisted or got ejected aren't
> there. For a multi-symbol cross-sectional strategy the bias is
> upward and non-trivial; flag it in `--note` and treat the resulting
> Sharpe as an upper bound. Single-symbol BTC strategies are unaffected.
> See [`AGENTS.md`](AGENTS.md) §10d.

### 5.2 Cross-sectional ranking
At each bar:
1. Compute a score per symbol (e.g. 30d return, momentum z-score).
2. Rank universe.
3. Long top decile, short bottom decile, weight `1/N`.

Hedges market beta. Most academic crypto papers show this works on weekly+ horizons; intraday is harder.

### 5.3 Beta neutralization
If your basket has net long bias, hedge it: short BTC futures sized to your portfolio beta, recomputed daily. Now PnL is alpha, not market direction.

### 5.4 Pairs trading
Trade the spread of two cointegrated symbols (e.g. BTC vs ETH leg-adjusted). Requires:
- Cointegration test on train window only.
- Recompute hedge ratio rolling, not static.
- Watch for regime breaks — cointegration is unstable.

---

## 6. Hyperparameter tuning — done correctly

The agent IS the hyperparameter optimizer. But within an iteration, you can also:

### 6.1 Grid / random search inside `generate_signals`
Bad idea. The harness's keep/revert is your search loop. Don't double-loop.

### 6.2 Cross-validated tuning on train-only
If a parameter genuinely needs fitting (e.g. ATR multiplier), use **walk-forward** *within the train slice*:
1. Split train into rolling folds.
2. For each fold, fit on past, evaluate on next.
3. Pick the param value with best median fold metric.
4. Apply to OOS.

Crucially: the choice is made entirely without seeing OOS.

### 6.3 Combinatorial purged k-fold (CPCV)
López de Prado's method to handle overlapping labels in time series. Overkill for most starting strategies, essential when you build complex labeling (e.g. triple-barrier) or many features.

### 6.4 `runner.optimize` — a *universe* of robust parameter plateaus
When you want to know which `PARAM_SPACE` regions are robust *before* spending
iterations one-at-a-time, run the optimizer:

```bash
uv run python -m runner.optimize strategies/<name> --params cci_period cci_threshold
```

What it does, and why it's safe:

- Searches the declared `PARAM_SPACE` (grid for ≤2 params, Sobol quasi-random
  for 3+), evaluating each candidate over **inner walk-forward folds run
  strictly inside the train slice** `[period_start, train_cutoff)`. It **never
  touches the reserved OOS tail** that `runner.iterate` uses for keep/revert,
  and the holdout is hard-capped out. This is the "tune within the train slice,
  choose without seeing OOS" rule of §6.2, enforced mechanically.
- Scores each candidate `mean(fold_sharpe) − 0.5·std(fold_sharpe)` with the
  same graded low-trades and low-time-in-position penalties as
  `composite_score` (so a sparse-but-real strategy is penalized, not nuked).
- Returns a **universe of plateaus** — connected high-score regions — not a
  single peak. A wide plateau (`n_configs` large, `span` wide) is robust; a
  `n_configs=1` spike is almost always overfit. **Pick the center of the widest
  high-score plateau**, not the single best score.

It is **read-only** to the iter loop: writes only to
`strategies/<name>/optimize/<id>/` (`universe.json`, `candidates.parquet`),
never to `best.json` / `history.jsonl` / `strategy.py` / `program.md`.

Workflow: `optimize` → read `universe.json` → set the chosen plateau center in
`DEFAULT_PARAMS` → run `runner.iterate` as usual. The OOS that judges the result
was never seen by the optimizer, so the keep/revert verdict stays honest.
This replaces the anti-pattern of hand-trying `cci_period=15`, `16`, `17`, …
one iteration at a time (AGENTS.md §6.1 calls those bad hypotheses).

---

## 7. Statistical hygiene — what to compute, not just optimize

These don't go into `composite` automatically but you should reason about them when interpreting results.

### 7.1 Probabilistic Sharpe Ratio (PSR)
> *"Probability that observed Sharpe exceeds a benchmark, given sample size, skew, and kurt."*

```
PSR = Φ( (SR_observed - SR_benchmark) · √(n-1) / √(1 - γ_3·SR + (γ_4-1)/4 · SR²) )
```
where `γ_3` is sample skew, `γ_4` is kurt, `n` is number of return obs. With short samples or fat-tailed returns, raw Sharpe overstates significance and PSR corrects for it.

### 7.2 Deflated Sharpe Ratio (DSR)
PSR adjusted for **the number of strategies you tried**. After 100 iterations, the expected Sharpe of the best — under the null of zero edge — is positive. DSR strips that bias out. Treat any best with `DSR < 0.95` (i.e. < 95% probability of true edge) as suspect.

### 7.3 Bootstrap confidence intervals on Sharpe
Stationary block bootstrap (Politis & Romano) on the OOS return series gives a 95% CI on Sharpe. If the lower bound includes 0, the result is not statistically distinguishable from luck — regardless of how cleanly the equity curve trends up.

### 7.4 Walk-forward stability
The harness can run multiple walk-forward windows (`--walk N`). A strategy whose Sharpe is stable across windows is more trustworthy than one with the same average Sharpe but high variance. Prefer the former even at slightly lower mean.

---

## 8. Costs, slippage, and capacity

The harness applies a flat taker fee (5.5 bps) and slippage (1 bp) by default. Reality is harsher and asymmetric.

### 8.1 Cost-aware decisions
A strategy generating 1000 round-trips/day on a 0.5-bp expected edge per trade is structurally negative. Internalize this in your hypothesis: *"this should add edge of >X bps per trade after fees"*. If X is unclear, the hypothesis is too vague.

### 8.2 Realistic slippage model
Slippage scales sub-linearly with size up to a point, then super-linearly. For each symbol estimate:
- Median bid-ask spread in bps.
- Top-of-book depth → max size before walking the book.
- Time-of-day liquidity profile.

If your strategy's notional approaches the top-of-book depth, real slippage will dominate fees. Reduce size or pick more liquid symbols.

### 8.3 Capacity estimation
A strategy that's profitable at $10K is not necessarily profitable at $1M. Estimate max capacity = `daily_volume_USD · participation_cap` (typically 1–5%). Strategies that require participation > 10% of volume don't scale.

---

## 9. What rarely helps (despite intuition)

- **Adding more indicators on top of an unprofitable base.** The base is wrong; ensemble of wrongs is still wrong.
- **Tightening a stop-loss to flatter the equity curve.** Usually destroys edge by cutting winners.
- **Reducing global leverage.** Sharpe is scale-invariant; you only changed the Y-axis.
- **Switching timeframes "to find clean signal".** The signal isn't in the bars; it's in the hypothesis. Wrong hypothesis on 5m is still wrong on 1h.
- **Optimizing on more data.** If 21 months don't show edge, 5 years probably won't either — you need a different hypothesis.
- **Adding ML for ML's sake.** A linear-classifier alpha that needs XGBoost to "find" usually doesn't generalize.

---

## 10. Decision tree — when to apply what

| Symptom in current best | First thing to try |
|---|---|
| Many trades, low hit rate, slightly negative composite | Cost-aware filter or size reduction; add ADX gate. |
| Few trades, lucky-looking equity | Increase sample by adding symbols or longer lookback before judging. |
| High train Sharpe, OOS near zero | Reduce degrees of freedom: fewer indicators, fewer params. |
| Equity dominated by 1–2 trades | Cap per-trade size; verify the trade isn't a data anomaly. |
| Steady losses in chop, gains in trends | Add trend filter (ADX or 200-MA slope). |
| Consistent loss in one regime, profit in another | Regime-conditional sizing or hard regime filter. |
| Mean-reverter blows up in trends | Time stop or trend-strength gate. |
| Trend-follower bleeds in chop | Vol-of-vol filter or ATR-expansion gate. |
| OOS strong, holdout weak | Stop iterating — you've overfit to OOS. Reset to an older best, change direction. |

---

## 11. References (worth reading once)

- López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018) — chapters 7 (cross-validation), 11 (backtest overfitting), 14 (deflated Sharpe).
- Bailey, López de Prado (2014). "The Deflated Sharpe Ratio." *Journal of Portfolio Management*.
- Harvey, Liu, Zhu (2016). "...and the Cross-Section of Expected Returns." Multiple-testing in finance.
- Bailey et al. (2014). "Pseudo-Mathematics and Financial Charlatanism." On overfit detection.
- Moskowitz, Ooi, Pedersen (2012). "Time-Series Momentum." Foundational for trend strategies.
- Asness, Moskowitz, Pedersen (2013). "Value and Momentum Everywhere." Cross-asset evidence.
- Politis, Romano (1994). "The Stationary Bootstrap." For Sharpe CIs.

---

**Last reminder.** This file is a vocabulary, not a recipe. The discipline is in the iteration loop, not in the menu of tricks. One hypothesis per iteration. State it in `--note`. Let the harness judge.
