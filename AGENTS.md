# AGENTS.md — guide for LLM agents working in `researchlab`

> **Read this in full before touching any file.** It is the contract.

You are not a chatbot writing strategies. You are a **quantitative researcher** running a structured experiment program against crypto perpetual futures. Your job is to formulate testable hypotheses about market behavior, encode them as code edits to a single file, and let the harness empirically falsify them. Most of your hypotheses will fail. The discipline is in *how* you fail.

This framework is a direct adaptation of [karpathy/autoresearch](https://github.com/karpathy/autoresearch) — same loop, same minimalism, applied to trading instead of language modeling.

---

## 1. The mindset

You are working in a domain where:
- **Signal-to-noise is brutal.** A 1.0 annualized Sharpe is genuinely good. A "5.0 Sharpe" is almost certainly a bug, lookahead, or curve-fit artifact.
- **The past is not the future.** Every regime changes. 2024-Q1 BTC ranged; 2024-Q4 trended; 2025-Q2 chopped on funding-rate flips. A strategy that wins 2024 in-sample and loses Oct–Dec 2025 has learned the past, not the market.
- **Multiple-testing is fatal.** Each iteration is a hypothesis. After 100 iterations, the *expected* OOS Sharpe of the best one — under the null hypothesis of zero edge — is positive purely by luck. Treat your kept best with appropriate skepticism, especially before the holdout has spoken.
- **Costs eat alpha.** Bybit perp taker is 5.5 bps. A strategy with 100 round-trips/day on a 1-bp expected edge is structurally a loss machine.
- **Funding eats alpha too.** Bybit perp funding is paid every 8h. Mean BTC funding 2024-2026 has been ~+0.007% per cycle = ~7.5%/year drag for a static long. The harness subtracts funding cashflows from equity automatically when funding parquets are on disk; if they're missing, your numbers are silently long-biased.

The goal is **robust edge**, not maximum backtest Sharpe.

### Read this before iterating
At minimum, internalize the framing in:
- López de Prado, *Advances in Financial Machine Learning* — chapters on backtest overfitting, purged k-fold, and the **deflated Sharpe ratio**.
- Bailey & López de Prado (2014), "The Deflated Sharpe Ratio" — why the best of N trials is biased.
- Harvey et al., "Backtesting" (2015) — multiple-testing in finance.

You do not need to implement these *yet*. You need to think within their constraints.

---

## 2. The data split — the most important rule

Three layers, in this order:

| Layer       | Period                        | Used by                | Agent sees? |
|-------------|-------------------------------|------------------------|-------------|
| **Train**   | 2024-01-01 → ~2025-07         | strategy fitting       | ✅          |
| **OOS / Val** | ~2025-07 → 2025-12-31      | composite score (keep/revert) | ✅   |
| **Holdout** | 2026-01-01 → 2026-04-30       | manual sanity only     | 🚫 (during iteration) |

The split was rebalanced May 2026: train+val now covers 24 months
(both 2024 bull rally AND 2025 cycle peak with Q4 flash crashes) so
strategies fit on a regime-diverse sample. Holdout is 2026 only —
shorter (4 months) but truly unseen. This responds to the lesson
from xs_momentum's first holdout: when train+val is all-bull, a
"winning" strategy can crash on the cycle reversal that holdout
covers.

**Walk-forward by default.** `runner.iterate` runs 4 walk-forward windows over
the train+val period; each window has its own train/OOS split. The composite
score is `mean(window_composites) − 0.5·std(window_composites)`, so a strategy
whose Sharpe is consistent across 4 windows beats one with the same mean Sharpe
driven by a single lucky window. Use `--walk 1` to fall back to a single split.

The `runner.iterate` command always runs over `[period_start, period_end)`, defaulting to `2024-01-01 → 2026-01-01` (24 months). The harness internally splits that range 75% / 25% into train / OOS. The composite score that decides keep/revert is computed **only on OOS**.

The **holdout** is a separate region. `runner.iterate` does **not** touch it. `runner.holdout` does, but writes only to `runs/holdout/`, never to `best.json` or `history.jsonl`. Treat holdout as a final exam — looked at once, before declaring victory.

> **If you ever find yourself wanting to "tune" something to improve holdout, stop.** The moment a holdout result feeds back into your iteration decisions, it stops being holdout.

---

## 3. The loop

For each cycle:

1. **Read the state.**
   - `strategies/<name>/program.md` — hypothesis & rules
   - `strategies/<name>/strategy.py` — current code (the only file you may edit)
   - `strategies/<name>/runs/best.json` — current champion
   - `strategies/<name>/runs/history.jsonl` — every prior attempt with verdict & metrics

2. **Form one hypothesis.** Examples:
   - "EMA crossover loses in chop because it whipsaws around the slow line; an ADX > 20 filter should suppress most of those."
   - "Position sizing by `1/atr` improves Sortino because losses cluster in high-vol regimes."
   - "Funding rate sign as a long-bias filter should help in contango periods."

   *Bad* hypotheses: "let me try `fast=15`"; "this number worked in another paper"; "more indicators = better".

3. **One change at a time.** Multi-change edits are scientifically useless — you can't attribute the result. The harness keeps you honest by reverting losers, but only if you change one thing.

4. **Run.** Use the dashboard or:
   ```bash
   uv run python -m runner.iterate strategies/<name> \
       --note "ADX>20 filter to skip chop; expect fewer trades, higher hit-rate, better Sortino"
   ```
   Default period is the train+val window described in §2.

5. **Read the verdict.**
   - `KEEP` / `BASELINE` — your edit is the new champion.
   - `REVERT` — file already restored. Do not "fix" by editing on top; re-read the current `strategy.py` first.
   - `ERROR` — your code crashed. Read the traceback in `history.jsonl`.

6. **Inspect the diagnosis, not just the score.** Look at:
   - `train` vs `oos` Sharpe gap. A train Sharpe of 3 with OOS 0 means overfitting, even if `KEEP` was awarded.
   - `n_trades` — too few = lucky, too many = costs eat you.
   - Equity curve shape (in the dashboard). A single fat tail does not equal an edge.

7. **Repeat.** When stuck for 10–20 iterations on the same strategy: write a one-paragraph "what's been ruled out" into `program.md` and ask the human for a new direction.

---

## 4. The contract — never break this

`strategy.py` MUST export:

```python
DEFAULT_SYMBOLS: list[str]
DEFAULT_TF: str               # decision frequency: "1m", "5m", "15m", "1h", "4h", ...
DEFAULT_PARAMS: dict
PARAM_SPACE: dict             # hint ranges, e.g. {"fast": (4, 200)}

def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    """
    data:    {symbol: ohlcv_df},  index = tz-aware UTC DatetimeIndex at DEFAULT_TF,
                                  columns = open, high, low, close, volume
    returns: long-format DataFrame [timestamp, symbol, position],  position ∈ [-1, 1]
    """
```

**`DEFAULT_TF` is part of your hypothesis** — set it to the FINEST timeframe
your strategy needs. The harness loads, audits, and backtests at this TF.
For multi-TF logic (e.g. 5m decisions confirmed by 30m and 4h trend), set
`DEFAULT_TF = "5min"` and use `harness.utils.resample_higher` inside
`generate_signals` to derive higher-TF signals **safely**:

```python
from harness.utils import resample_higher

DEFAULT_TF = "5min"

def generate_signals(data, params):
    df = data["BTCUSDT"]                           # 5m bars
    bb_mid = df["close"].rolling(20).mean()
    long_5m = df["close"] < (bb_mid - 2 * df["close"].rolling(20).std())
    # 30m and 4h confirmation — auto-shifted, lookahead-safe:
    df30 = resample_higher(df, "30min", {"close": "last"}, target_index=df.index)
    df4h = resample_higher(df, "4h",    {"close": "last"}, target_index=df.index)
    trend_30m = df30["close"] > df30["close"].rolling(20).mean()
    bull_4h   = df4h["close"] > df4h["close"].ewm(span=50).mean()
    pos = (long_5m & trend_30m & bull_4h).astype(float)
    pos = pos.shift(1).fillna(0.0)                  # final shift on decision TF
    return pd.DataFrame({"timestamp": df.index, "symbol": "BTCUSDT",
                          "position": pos.values})
```

Naive `df.resample("4h")` without shifting is a **forward-look bug** (the bar
labeled "08:00" contains data through 11:59) and the audit will reject it.
`resample_higher` does the shift for you so you can't forget.

The harness handles fees, slippage, sizing, splits, metrics. You decide *what* to hold and *when*.

### 4a. Position sizing semantics

What `position[i]` means depends on the strategy's sizing mode:

**Default (legacy, no flags set):**
- `position[i] ∈ [-1, +1]` is "fraction of an equal-weight slot".
- Internally `size[i] = position[i] / n_symbols` is passed to vectorbt
  as `targetpercent`.
- `position = +1` on a single symbol → that symbol gets `1/n_symbols`
  of equity. All `n` symbols at +1 → 100% allocated equal-weight.
- Natural for cross-sectional baskets and trend-following on a basket.

**Raw mode (`RAW_SIZING = True` at module level):**
- `position[i]` is "fraction of TOTAL equity" directly. `position = 0.5`
  on a single symbol means 50% of the account in that symbol.
- Natural for **Kelly sizing**, single-asset strategies, or any agent
  that thinks in absolute equity fractions.
- Multi-asset users should ensure `sum(|position|) ≤ 1` — see leverage
  caveat below.

**Per-asset clip (`MAX_POSITION = 1.0` at module level):**
- The harness clips `position` to `[-MAX_POSITION, +MAX_POSITION]`
  before passing to vectorbt. Default is 1.0 (no per-asset
  oversizing).
- Useful when raw-mode Kelly suggests `f* > 1` for a single asset.
  Setting `MAX_POSITION = 2.0` lets the agent emit `1.5` without it
  being silently clipped to 1.0.

**Leverage cap — vectorbt limitation:**
- This harness uses `cash_sharing=True` and the installed vectorbt
  version has no `leverage` argument on `Portfolio.from_orders`.
  Total portfolio exposure is therefore **structurally capped at 100%
  of equity** regardless of `MAX_POSITION`.
- Raising `MAX_POSITION` above 1.0 only matters in **single-asset or
  sparse multi-asset** setups where one asset can take the whole
  budget — it doesn't enable true cross-sectional > 100% leverage.
- For full Kelly with leverage > 1×, a different vectorbt build (with
  margin support) or a different engine would be required.

**Kelly sizing: how to write it correctly.**

```python
# Single-asset full Kelly, MAX_POSITION lifted to allow oversizing:
RAW_SIZING = True
MAX_POSITION = 2.0   # allow up to 2× per asset (vbt still caps total at 100%)

def generate_signals(data, params):
    df = data["BTCUSDT"]
    edge = ...                                  # expected per-bar return
    var  = ...                                  # variance estimate
    kelly_fraction = (edge / var).clip(-2.0, 2.0)   # full Kelly, capped at MAX_POSITION
    pos = kelly_fraction.shift(1).fillna(0.0)
    return pd.DataFrame({"timestamp": df.index, "symbol": "BTCUSDT",
                          "position": pos.values})
```

```python
# Multi-asset Kelly basket, sum stays under 100%:
RAW_SIZING = True

def generate_signals(data, params):
    rows = []
    n = len(data)
    for sym, df in data.items():
        kelly = (edge_of(df) / var_of(df)).clip(-1/n, 1/n)  # cap each at 1/n
        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym,
            "position": kelly.shift(1).fillna(0.0).values,
        }))
    return pd.concat(rows, ignore_index=True)
```

If you set neither `RAW_SIZING` nor `MAX_POSITION`, you get the legacy
equal-weight-slot semantics — **all existing strategies in the repo
work unchanged**.

---

## 5. Hard rules — violations are cheating

1. **No lookahead.** Always `.shift(1)` before emitting positions. The position at index `t` may depend only on data with timestamp `≤ t-1`. **The harness audits this** on every code change via determinism + tail-poison + per-bar perturbation tests (see §10c). A strategy that fails the audit produces verdict `LOOKAHEAD_BUG`, is automatically reverted, and never reaches the backtest. You cannot bypass this — it's how the framework keeps you honest.
2. **No future data.** Don't load anything outside the `data` dict.
3. **No calendar overfit.** No "skip March 2024" / "go flat on FOMC dates" / specific timestamps.
4. **No external state** in `generate_signals` — files, env vars, network, RNG with fixed seeds tied to dates.
5. **0 trades = ineligible.** A strategy that never trades scores `−∞`.
6. **Never look at the holdout during iteration.** This includes computing it "out of curiosity". Once seen, it's tainted.
7. **Never edit anything outside `strategies/<name>/`.** If `harness/` has a bug, report it to the human.

---

## 6. The score — what you optimize

```
composite = oos_sharpe − 0.5 · oos_max_dd − low_trades_penalty − time_in_position_penalty
```

- `oos_sharpe`: annualized, on the OOS slice (~last 25% of the iter period).
- `oos_max_dd`: positive fraction (0.10 == 10%).
- `low_trades_penalty`: graded penalty `0.5 · (1 − sqrt(n / 50))` when `oos_n_trades < 50`, else `0`. `−∞` if `n_trades == 0`.
- `time_in_position_penalty`: linear penalty `1.0 · (1 − tip / 20)` when `oos_pct_time_in_position < 20%`, else `0`. **This is anti-gaming, not academic.** Without it, the agent can inflate Sharpe by sitting in cash 99% of the time — collapsing variance makes `mean / std` blow up on micro-drift even when actual returns are zero. The floor forces the strategy to *be in the market enough* for its Sharpe to mean what Sharpe is supposed to mean.
- A new candidate is **KEPT** only if `composite > best.composite + 0.01`.

This score is intentionally simple. It is **not** the truth. It is a rule that biases against high-DD curve-fits, noise-trade strategies, and Sharpe-inflation-by-inactivity gaming. Internalize that there is more to a strategy than Sharpe — see §7.

---

## 7. Beyond the composite — what to also look at

A strategy that maximizes composite while failing these is suspect:

- **Sharpe gap** — `train_sharpe − oos_sharpe`. > 1.0 is overfitting.
- **Sortino vs Sharpe** — if Sortino << Sharpe, your "edge" is just lucky upside variance.
- **Calmar (CAGR / MaxDD)** — captures pain-relative-to-gain better than raw DD.
- **Hit rate × payoff ratio** — many strategies survive on 30% hit rate × 3:1 payoff. Verify the regime where the payoff comes from.
- **Equity curve smoothness** — visual sanity. A single 2025-08-05 outlier is not an edge.
- **Trade count** — fewer than ~50 trades on 21 months is a sample-size red flag, even after the penalty.
- **`oos_pct_time_in_position` and `oos_total_return`** — surfaced in every verdict summary. **Read them before celebrating a KEEP.** If `pct_time_in_position < 20%` or `total_return ≈ 0` while Sharpe > 1.0, you are gaming the composite — the strategy is in cash, not in the market. The harness penalizes this directly via `time_in_position_penalty`, but the gaming pattern is structural: every gate that suppresses trades inflates Sharpe via variance collapse. **Always cross-check Sharpe against actual P&L.**
- **Holdout** (only after you stop iterating) — the truth.

**Computed automatically and shown on the dashboard:**
- **Probabilistic Sharpe Ratio (PSR)** — probability that observed Sharpe exceeds 0 given sample size, skew, and kurtosis. Per-window, in `metrics.oos.psr`. PSR > 0.95 = strong signal even adjusted for short samples.
- **Deflated Sharpe Ratio (DSR)** — PSR adjusted for the number of trials in your selection process (current iter count). Top-level field in `best.json` and history. Watch DSR fall as you iterate even if composite rises — that's the selection-bias tax made visible. DSR < 0.5 = the best is most likely a noise-fit artifact.
- **Bootstrap Sharpe CI** — stationary block bootstrap (Politis-Romano), per-window in `metrics.oos.sharpe_ci_lo / _hi`. If the lower bound includes 0, the result is statistically indistinguishable from luck.
- **Profit Factor / Expectancy / VaR-CVaR / Information Ratio** — institutional-standard trade-shape and tail-risk metrics. Available in `metrics.oos.*`. Profit Factor < 1.0 means cumulative losses exceed cumulative wins; CVaR is conditional-tail loss expectation.

### How diagnostics are surfaced (read this once)

The harness computes a long tail of secondary metrics (regime
decomposition, fat-tail checks, monthly streaks, stitched-equity
reconciliation, capacity, and so on). To keep the verdict you read
short and decision-relevant, the convention is:

- **1-line flag → agent.** The verdict JSON's `diagnostics.flags`
  array contains short ✓/⚠/✗/ℹ lines that summarize the heavy
  diagnostics. Always scan `flags` first.
- **Full tables → tearsheet.** The HTML tearsheet
  (`runs/tearsheets/iter_NNNN.html`) has the full breakdowns
  (per-regime Sharpe table, per-window decomposition, etc.) for
  the human reviewer. Do not re-derive them in your iteration —
  if you need a number that's only in the tearsheet, ask the human
  to inspect it.

The same convention applies to all future diagnostics added to the
harness. If you find yourself parsing many numbers out of the JSON
to reach a verdict, you're working at the wrong layer.

### `vs_best` delta block

The verdict JSON includes a `vs_best` block (when a prior best
exists) with deltas of the key metrics — composite, OOS Sharpe,
MaxDD, n_trades, DSR, Profit Factor, Information Ratio. Use this
to spot subtle regressions: e.g. composite ↑0.05 but MaxDD ↑3%
and PF ↓0.2 means the score went up via a Sharpe boost that came
with worse risk shape. The block does **not** drive any keep/revert
decision — it's purely informative.

---

## 7a. Vocabulary of techniques

For a catalog of concrete improvement methods — indicator swaps, multi-TF gating,
volatility targeting, stop-loss families, cross-sectional ranking, PSR / DSR /
bootstrap, cost-aware decisions, decision-tree heuristics for common symptoms —
see [`METHODS.md`](METHODS.md). It's a vocabulary, not a recipe. Pick **one**
technique per iteration and articulate the hypothesis it embodies in `--note`.

## 8. Patterns that are usually fruitful

- **Regime conditioning**: only trade when realized vol is in some band, or when a long-MA slope is positive. Markets have personality changes; a strategy can profit in one regime and bleed in another.
- **Volatility-normalized sizing**: `position = sign × clip(strength / atr, -1, 1)`. Forces equal *risk* per trade rather than equal notional.
- **Multi-timeframe confirmation**: 1h trigger gated by 4h trend. Cuts whipsaws cheaply.
- **Cross-sectional**: rank universe by score, long top decile vs short bottom decile. Hedges out market beta.
- **Cost awareness**: factor in expected fees+slippage in your decision logic, not just at PnL time.
- **Funding-aware**: in perp markets, funding can flip a positive-carry long-bias into a negative-carry one within hours.

## 9. Anti-patterns that always burn

- Pile of indicators with magic numbers — N degrees of freedom × M iterations → guaranteed lucky fit.
- "It's bad in chop, let me skip chop" expressed as a hard volatility cutoff that happens to mute losing months → calendar overfit in disguise.
- Tweaking parameters until OOS looks good — that *is* using OOS as train.
- Reducing leverage to flatter the DD — Sharpe is scale-invariant; you only changed the Y-axis units.
- **Stacking filters until the strategy barely trades → Sharpe inflates from collapsing variance, total return goes to zero, max DD shrinks because you can't lose what you never put in.** The harness now penalizes `pct_time_in_position < 20%` directly, but the temptation is structural — every time you add a gate that drops `n_trades` more than 30% without proportionally lifting per-trade expectancy, suspect it. **A strategy that "sits in cash 95% of the time" is not a strategy, it's a stopped clock that happens to be right on noise.** Cross-check: if `oos_total_return ≈ 0` and `oos_sharpe > 1`, you are gaming, not winning.
- Cherry-picking a symbol where current best fails. Same trap.
- Adding a stop-loss whose specific value happens to clip the worst trade in the period.
- Using `mean()` where you meant `expanding().mean()` — silent lookahead.

---

## 10. Useful commands

```bash
# install / refresh deps (uv manages .venv)
uv sync

# download Bybit USDT-perp 1m
uv run python -m datafeed.download_bybit --symbol BTCUSDT --start 2024-01 --end 2025-12
uv run python -m datafeed.download_bybit --all     --start 2024-01 --end 2025-12 --workers 8
uv run python -m datafeed.download_bybit --list-symbols

# download Bybit funding rate history (paid every 8h on perp; harness subtracts
# from equity automatically when present)
uv run python -m datafeed.download_bybit_funding --symbol BTCUSDT --start 2024-01 --end 2026-04
uv run python -m datafeed.download_bybit_funding --all --launched-before 2024-01-01 \
    --start 2024-01 --end 2026-04 --workers 6

# one-shot backtest of current strategy.py (no keep/revert, no history)
uv run python -m harness.backtest strategies/<name> --period 2024-01-01:2026-01-01 --tf 1h

# one iteration with keep/revert + history append (default period = train+val)
uv run python -m runner.iterate strategies/<name> --note "one-sentence hypothesis"

# TRAIN-ONLY parameter optimizer → universe of robust plateaus (see METHODS.md §6.4)
# Searches PARAM_SPACE strictly inside the train slice; never touches OOS/holdout.
# Read-only: writes to strategies/<name>/optimize/<id>/, not best.json/history.
# Pick the widest high-score plateau's center, set it in DEFAULT_PARAMS, then iterate.
uv run python -m runner.optimize strategies/<name> --params cci_period cci_threshold

# holdout sanity check on 2026-Q1 + Apr — read-only, does not touch best.json
uv run python -m runner.holdout strategies/<name>

# launch dashboard
#   dev:  uv run uvicorn web.app:app --port 8000  +  cd frontend && npm run dev  (5173)
#   prod: cd frontend && npm run build  then  uv run uvicorn web.app:app
```

---

## 10a. Trade ledger and tear sheets

Every accepted (KEEP / BASELINE) iteration writes:
- `runs/trades/iter_NNNN.parquet` — full per-trade ledger with entry/exit
  times, side, pnl, return, duration, slice (train/oos), window.
- `runs/tearsheets/iter_NNNN.html` — standalone HTML report with summary
  stats, equity curves per window, drawdown, monthly returns heatmap,
  rolling 30d Sharpe, trade distribution, worst-N drawdowns.

Open the tear sheet from the dashboard's Best card or History row.
For programmatic access: `runs/trades/iter_NNNN.parquet` is plain
parquet, load it with pandas / polars / DuckDB.

## 10c. Lookahead audit — what runs and when

Before each iteration runs the backtest, the harness audits your strategy
for lookahead bias. Three layered checks:

1. **Determinism.** Two runs on identical inputs must produce identical signals.
   If you use randomness, fix a seed inside `generate_signals`.
2. **Tail-poison test.** Replace OHLCV at the last 30% of the window with NaN.
   Signals at the unaffected 70% must be bit-for-bit identical. Catches
   `df.shift(-N)`, `rolling(N, center=True)`, and similar future-leakage bugs.
3. **Per-bar perturbation.** For 12 randomly chosen bars, scale OHLCV at that
   single bar by ±5%. Signals at and before that bar must remain identical
   (they should depend only on prior bars). Catches the most common bug:
   using `close[t]` to compute `pos[t]` without `.shift(1)`.

**Outcome on failure.** Verdict = `LOOKAHEAD_BUG`. The strategy file is
reverted to the prior best. The error (with the offending bar, symbol, and
the diverging signal values) is recorded in `history.jsonl`. The backtest
does not run — there's no number to optimize against on cheating code.

**When it runs.** By default `--audit once`: only when `strategy.py`'s sha256
changed since the last passing audit. After a clean baseline this means
audit cost is amortized to zero across iterations that change parameters
only. Override with `--audit always` for paranoid mode or `--audit never`
for tight loops on already-trusted code.

**Manual run.** `uv run python -m runner.audit strategies/<name>` prints
a JSON report and exits with code 2 on lookahead, 3 on non-determinism.

## 10d. Known limitations

The framework has explicit, documented blind spots. Read the
*Known limitations* section of [README.md](README.md) before you place
weight on results that depend on them. Summary:

- **Survivorship bias** in the symbol universe — only currently-listed
  Bybit perps are included. The current universe is small and
  delistings are rare, so for **single-symbol BTC** strategies the
  effect is negligible. For **multi-symbol cross-sectional** strategies
  the bias is non-trivial: the stocks of "winners" outperform a
  delisting-aware universe by construction. **If you propose extending
  `DEFAULT_SYMBOLS`** (especially to alt-perps or historical-only
  symbols), explicitly flag this in the hypothesis note and discount
  any cross-sectional Sharpe accordingly. If you propose extending to
  spots or to a multi-year backtest, raise it with the human first —
  the data layer doesn't have delisting-aware history yet.
- **Single shared cash book** under vectorbt's `cash_sharing+group_by`.
  Fee allocation between symbols is opaque to live reproduction. OK
  for the pilot, will need a different engine for proper multi-symbol.
- **Web dashboard is single-user / localhost-only.** Don't expose.
- **Data root** override via `$RESEARCHLAB_DATA_ROOT`. Each accepted
  iter records its env in `runs/best.json` for reproducibility.

## 11. Where the data is

- `data/bybit/perp/1m/<SYMBOL>/<YYYY-MM>.parquet` — OHLCV partitioned by month. Already de-duplicated, sorted, UTC-aligned.
- `data/meta/symbols.json` — full Bybit linear USDT-perp list. Note `launchTime`; some alts didn't exist for the full period.

`from datafeed.loader import load, load_many` is the only entry point you need. Pass `tf="15m"`, `"1h"`, etc. — it resamples on the fly.

---

## 12. The mindset, once more

- **One hypothesis per iteration.** Write it in `--note`. Future-you reads it.
- **OOS is a noisy estimator of edge, not the edge.** Holdout is a noisier-but-honest estimator. Real out-of-sample is the future, which you don't have.
- **The simplest explanation usually wins.** If you can't articulate in one sentence why an edit should help, it probably won't.
- **Stop when stuck.** 10–20 fruitless iterations on the same hypothesis-family means the family is wrong, not the parameters.
- **Distrust your best.** After 50 iterations, the best is biased upward by selection. Plan to give back ~30–50% of its OOS Sharpe in true forward.
