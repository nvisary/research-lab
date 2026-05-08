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
| **Train**   | 2024-01-01 → ~2025-05         | strategy fitting       | ✅          |
| **OOS / Val** | ~2025-05 → 2025-09-30      | composite score (keep/revert) | ✅   |
| **Holdout** | 2025-10-01 → 2026-04-30       | manual sanity only     | 🚫 (during iteration) |

**Walk-forward by default.** `runner.iterate` runs 4 walk-forward windows over
the train+val period; each window has its own train/OOS split. The composite
score is `mean(window_composites) − 0.5·std(window_composites)`, so a strategy
whose Sharpe is consistent across 4 windows beats one with the same mean Sharpe
driven by a single lucky window. Use `--walk 1` to fall back to a single split.

The `runner.iterate` command always runs over `[period_start, period_end)`, defaulting to `2024-01-01 → 2025-10-01`. The harness internally splits that range 75% / 25% into train / OOS. The composite score that decides keep/revert is computed **only on OOS**.

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
composite = oos_sharpe − 0.5 · oos_max_dd − low_trades_penalty
```

- `oos_sharpe`: annualized, on the OOS slice (~last 25% of the iter period).
- `oos_max_dd`: positive fraction (0.10 == 10%).
- `low_trades_penalty`: `0.5` if `oos_n_trades < 50`, else `0`. `−∞` if `n_trades == 0`.
- A new candidate is **KEPT** only if `composite > best.composite + 0.01`.

This score is intentionally simple. It is **not** the truth. It is a rule that biases against high-DD curve-fits and noise-trade strategies. Internalize that there is more to a strategy than Sharpe — see §7.

---

## 7. Beyond the composite — what to also look at

A strategy that maximizes composite while failing these is suspect:

- **Sharpe gap** — `train_sharpe − oos_sharpe`. > 1.0 is overfitting.
- **Sortino vs Sharpe** — if Sortino << Sharpe, your "edge" is just lucky upside variance.
- **Calmar (CAGR / MaxDD)** — captures pain-relative-to-gain better than raw DD.
- **Hit rate × payoff ratio** — many strategies survive on 30% hit rate × 3:1 payoff. Verify the regime where the payoff comes from.
- **Equity curve smoothness** — visual sanity. A single 2025-08-05 outlier is not an edge.
- **Trade count** — fewer than ~50 trades on 21 months is a sample-size red flag, even after the penalty.
- **Holdout** (only after you stop iterating) — the truth.

**Computed automatically and shown on the dashboard:**
- **Probabilistic Sharpe Ratio (PSR)** — probability that observed Sharpe exceeds 0 given sample size, skew, and kurtosis. Per-window, in `metrics.oos.psr`. PSR > 0.95 = strong signal even adjusted for short samples.
- **Deflated Sharpe Ratio (DSR)** — PSR adjusted for the number of trials in your selection process (current iter count). Top-level field in `best.json` and history. Watch DSR fall as you iterate even if composite rises — that's the selection-bias tax made visible. DSR < 0.5 = the best is most likely a noise-fit artifact.
- **Bootstrap Sharpe CI** — stationary block bootstrap (Politis-Romano), per-window in `metrics.oos.sharpe_ci_lo / _hi`. If the lower bound includes 0, the result is statistically indistinguishable from luck.

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
uv run python -m harness.backtest strategies/<name> --period 2024-01-01:2025-10-01 --tf 1h

# one iteration with keep/revert + history append (default period = train+val)
uv run python -m runner.iterate strategies/<name> --note "one-sentence hypothesis"

# holdout sanity check on 2025-Q4 + 2026-Q1+Apr — read-only, does not touch best.json
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
  Bybit perps are included. Multi-symbol cross-sectional results are
  biased upward; single-symbol BTC results are not.
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
