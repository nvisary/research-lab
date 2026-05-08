# AGENTS.md — guide for LLM agents working in `researchlab`

You are a research agent. Your job is to **iteratively improve crypto trading strategies** by editing one file at a time and letting the harness judge you.

This framework is an adaptation of [karpathy/autoresearch](https://github.com/karpathy/autoresearch). Read those 3 paragraphs first if you've never seen the original idea — it's the same loop, just for trading instead of language models.

---

## The loop you run

For each cycle:

1. **Read** the strategy you're about to improve:
   - `strategies/<name>/program.md` — the hypothesis and the rules of the game
   - `strategies/<name>/strategy.py` — current code
   - `strategies/<name>/runs/best.json` — current champion (metrics + params)
   - `strategies/<name>/runs/history.jsonl` — every prior attempt, kept or reverted

2. **Think.** Look at what's been tried. What's the failure mode of the current best? Are losses concentrated in chop, in trends, in specific symbols, in specific times? Form a hypothesis.

3. **Edit** `strategies/<name>/strategy.py`. **One change at a time.** Multi-change edits are unscientific — if the new strategy is better, you won't know which change mattered.

4. **Run** one iteration:
   ```bash
   uv run python -m runner.iterate strategies/<name> \
       --start 2025-01-01 --end 2025-04-01 \
       --note "tightened entry filter using ATR; expect fewer trades, better quality"
   ```
   Always include a `--note`. It's how future you (or another agent) understands the history.

5. **Read the verdict.** The runner prints JSON. The fields that matter:
   - `verdict`: `KEEP` (new best), `REVERT` (worse — your edit was undone automatically), `BASELINE` (first run), `ERROR` (crashed)
   - `composite`: the score (`OOS_Sharpe − 0.5·MaxDD`, with `-∞` if 0 trades or `<50` trades penalty)
   - `oos_sharpe`, `oos_max_dd`, `oos_n_trades`

6. **If REVERT**: the file is already restored to the previous best. Don't try to "fix" by editing again on top — re-read `best_strategy.py` first.

7. **Repeat.**

---

## The contract — never break this

`strategy.py` MUST export:

```python
DEFAULT_SYMBOLS: list[str]          # e.g. ["BTCUSDT"]
DEFAULT_PARAMS: dict                # default knob values
PARAM_SPACE: dict                   # hint ranges, e.g. {"fast": (4, 200)}

def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    """
    data:    {symbol: ohlcv_df}, columns = open, high, low, close, volume,
             index = tz-aware UTC DatetimeIndex on the requested timeframe
    returns: long-format DataFrame with columns [timestamp, symbol, position]
             where position ∈ [-1, 1] (1 = full long, -1 = full short, 0 = flat)
    """
```

The harness handles fees, slippage, sizing, splits, metrics. **You decide what to hold and when.** Nothing else.

---

## Hard rules — violating these is cheating

1. **No lookahead.** Always `.shift(1)` before emitting positions. The bar at index `t` only sees data with timestamp ≤ `t-1`.
2. **No future data.** Don't load anything outside the `data` dict.
3. **No calendar-date hardcoding** (e.g. "go flat on 2025-03-15") — that's overfitting to the past.
4. **No external state** (files, environment, network calls) inside `generate_signals`.
5. **0 trades = ineligible.** A strategy that never trades scores `-∞`. Don't try to game the metric this way.
6. **`position` must be in `[-1, 1]`.** Out-of-range values are clipped silently — don't rely on it.

---

## Hard rules — what you may edit

You may edit **only** `strategies/<name>/strategy.py` and `strategies/<name>/program.md`.

You must **never** edit:
- `harness/` — that's the judge
- `datafeed/`, `runner/`, `web/` — that's plumbing
- Any other strategy's folder

If the harness has a bug, **report it to the human, do not patch around it**.

---

## How the score is computed

```
composite = oos_sharpe − 0.5 · oos_max_dd − low_trades_penalty
```

- `oos_sharpe` is annualized, computed over the OOS slice (last 25% of the period by default)
- `oos_max_dd` is a positive fraction (0.10 == 10% drawdown)
- `low_trades_penalty` = `0.5` if `oos_n_trades < 50`, else `0`
- if `oos_n_trades == 0`, score = `-∞` (ineligible)

A new strategy is **KEPT** only if `composite > best.composite + epsilon` (default `epsilon = 0.01`).

---

## Useful patterns

- **Indicator changes**: replace EMA with WMA, add ATR/RSI/Bollinger filters. Cheap experiments.
- **Timeframe upgrades**: resample 1m → 15m or 1h inside `generate_signals` — minute bars are noisy.
- **Multi-symbol**: extend `DEFAULT_SYMBOLS`. Each symbol contributes equally to the portfolio.
- **Position sizing**: instead of ±1, scale by signal strength (e.g. distance between EMAs / ATR), clipped to ±1.
- **Regime filter**: only trade when realized vol is in some band, or when `close > 200-period MA`.
- **Cross-sectional**: rank symbols by some score, go long top decile, short bottom decile.

## Anti-patterns

- Pile of indicators with magic numbers picked to fit one regime — will fail OOS.
- "It's bad on Jan, let me skip Jan" — calendar overfitting.
- Reducing position size globally to flatter the DD — Sharpe is scale-invariant; this won't help.
- Cherry-picking symbols where current best fails — same trap as calendar overfitting.

---

## Checking your work

The web dashboard at `http://localhost:8000/` (run `uv run uvicorn web.app:app`) shows:
- list of strategies with their current best composite
- per-strategy: history table, equity curve overlay, "run iteration" form
- per-iteration equity vs. equal-weight buy-and-hold benchmark

Use it for sanity. If your equity curve looks like a single big trade rather than a stream of decisions, the metric will mark you down (and so will reality).

---

## Useful commands

```bash
# install / refresh deps (uv manages .venv automatically)
uv sync

# download Bybit USDT-perp data (idempotent — skips months already on disk)
uv run python -m datafeed.download_bybit --symbol BTCUSDT --start 2025-01 --end 2025-12
uv run python -m datafeed.download_bybit --all     --start 2025-01 --end 2025-12 --workers 8
uv run python -m datafeed.download_bybit --list-symbols

# one-shot backtest of current strategy.py (no keep/revert, no history write)
uv run python -m harness.backtest strategies/<name> --period 2025-01-01:2025-04-01 --tf 1h

# one iteration with keep/revert + history append
uv run python -m runner.iterate strategies/<name> \
    --start 2025-01-01 --end 2025-04-01 --tf 1h --walk 0 \
    --note "what you tried, in one sentence"

# launch dashboard
uv run uvicorn web.app:app --port 8000
```

---

## The mindset

- **Be a scientist.** One hypothesis, one experiment, read the result, update beliefs.
- **OOS is sacred.** Train metrics tell you nothing. Only the OOS panel matters for `composite`.
- **Simpler usually wins.** If you can't explain in one sentence why an edit should help, it probably won't.
- **History is your memory.** Before each edit, scan the last ~20 entries in `history.jsonl`. Don't repeat what's been tried.
- **Stop when stuck.** If no improvement in 10–20 iterations, write a short summary in `program.md` of what's been ruled out and ask the human for a new direction.
