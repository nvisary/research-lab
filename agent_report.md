# Agent run report — strategies/sma_cross

Session date: 2026-05-08. 19 iterations executed (iter 12 through iter 30).
Starting champion: iter 8 (composite -0.58). Ending champion: **iter 27
(composite +1.34, OOS Sharpe +2.75, DSR 0.78)**.

## 1. Iteration summary

| iter | verdict | composite | OOS Sharpe | DSR | symbol | TF | hypothesis |
|------|---------|-----------|------------|------|--------|-----|------------|
| 12 | REVERT | -1.49 | +0.16 | 0.26 | BTC | 1h | 4h SMA(50) trend gate |
| 13 | REVERT | -1.87 | -0.62 | 0.11 | BTC | 1h | low-vol regime filter (ATR/close < q20 skip) |
| 14 | KEEP   | -0.21 | +1.03 | 0.40 | BTC | 4h | switch decision TF 1h→4h |
| 15 | REVERT | -1.08 | +0.35 | 0.29 | BTC | 4h | slower SMAs 30/150 |
| 16 | REVERT | -1.38 | -0.45 | 0.09 | BTC | 4h | faster SMAs 15/75 |
| 17 | KEEP   | +0.36 | +0.81 | 0.35 | BTC | 4h | drop cooldown (6→0) |
| 18 | REVERT | +0.26 | +0.91 | 0.37 | BTC | 4h | target_daily_vol 0.02→0.03 |
| 19 | REVERT | -0.78 | -0.08 | 0.14 | BTC | 4h | 1d SMA(50) trend gate |
| 20 | REVERT | -0.21 | +0.41 | 0.24 | BTC | 4h | continuous strength = clip(gap*50, -1, 1) |
| 21 | REVERT | -4.42 | -2.56 | 0.01 | ETH | 4h | switch single sym BTC→ETH |
| 22 | REVERT | -2.30 | -1.14 | 0.03 | BTC+ETH | 4h | multi-sym basket BTC+ETH |
| 23 | REVERT | +0.02 | +0.55 | 0.21 | BTC | 4h | vol_lookback 42→84 |
| 24 | KEEP   | +0.56 | +1.18 | 0.36 | BTC | 4h | vol_lookback 42→21 |
| 25 | REVERT | +0.35 | +0.97 | 0.29 | BTC | 4h | vol_lookback 21→12 |
| 26 | REVERT | +0.45 | +0.93 | 0.27 | BTC | 4h | vol_lookback 21→30 |
| 27 | **KEEP** | **+1.34** | **+2.75** | **0.78** | BTC | 4h | **long-only (drop shorts)** |
| 28 | REVERT | +0.11 | +1.91 | 0.67 | BTC | 4h | EMA instead of SMA |
| 29 | REVERT | +0.45 | +1.90 | 0.44 | BTC | 4h | slow MA 100→80 |
| 30 | REVERT | +0.54 | +2.11 | 0.61 | BTC | 4h | slow MA 100→120 |

Three KEEPs in 19 iterations (14, 17, 24, 27 — actually four). Composite path:
-0.58 → -0.21 → +0.36 → +0.56 → **+1.34**.

## 2. Champion (iter 27)

**File**: `strategies/sma_cross/strategy.py`

**Params**:
```python
DEFAULT_SYMBOLS = ["BTCUSDT"]
DEFAULT_TF = "4h"
DEFAULT_PARAMS = {
    "fast": 20,
    "slow": 100,
    "vol_lookback": 21,        # ~3.5 days of 4h bars
    "target_daily_vol": 0.02,
    "cooldown_bars": 0,
    "bars_per_day": 6,
}
```

**Logic**: SMA(20) > SMA(100) → long with size = clip(0.02 / realized_daily_vol, 0, 1). Otherwise flat. Position shifted by 1 bar.

**WF window breakdown (OOS)**:
- w0: Sharpe +1.46, DD 6.5%, 52 trades
- w1: Sharpe +6.37, DD 7.1%, 38 trades
- w2: Sharpe +2.93, DD 7.8%, 10 trades
- w3: Sharpe +0.26, DD 4.8%, **1 trade** (this is the weakness)

Mean WF Sharpe +2.76, std 2.34. Composite penalized -0.5×std.

**Why it works**: BTC at 4h with a 20/100 SMA cross is a classic medium-term
trend follower. Adding the long-only constraint removes structurally negative
short legs (BTC's strong up-bias 2024-2025 + funding drag). Vol-targeting keeps
DD bounded (max OOS 7.8%).

## 3. What was ruled out (and why)

**External trend filters, every flavor.** 4h SMA(50) gate, 1d SMA(50) gate, low-vol
regime gate — all degraded composite by 0.3 to 1.3 points. The SMA cross itself
is already a trend filter; layering a slower one cuts good entries faster than
it cuts bad ones. The pattern is consistent: any filter that reduced trade count
without specifically targeting losing trades made things worse.

**Symbol substitution / diversification.** Switching to ETH alone collapsed
composite to -4.4. BTC+ETH basket got -2.3. ETH at 4h with these exact params
has very different behavior — the 20/100 cross is BTC-tuned. A multi-symbol
strategy needs per-symbol parameter calibration or cross-sectional ranking,
neither of which fit "one change" iterations. Worth a fresh strategy spawn.

**Continuous position sizing.** Replacing ±1 (or 0/1) with `clip(gap × k, -1, 1)`
intuitively should "scale conviction with gap size" but in practice reduced the
average notional position and lost edge. On a single-asset trend strategy,
binary direction × volatility-target size already provides good risk-scaled
exposure; adding a third multiplier is over-conditioning.

**Local parameter sweeps near the optimum.** vol_lookback ∈ {12, 21, 30, 42, 84}
all gave similar OOS Sharpes (0.93 to 1.18) but composite differed because of
WF variance. The 21 sweet-spot is suspicious — it's borderline overfit. Same
for slow=100 vs 80/120 (all yield OOS Sharpe ~1.9–2.7 in the long-only frame).
**Treat all parameter values within ±50% as equivalent for forward expectations.**

## 4. Recommendations for the next session

1. **Holdout the iter-27 champion immediately.** That's the most informative
   single experiment available — DSR is 0.78 which is moderate evidence; holdout
   on 2025-10 → 2026-04 will tell you whether the long-only-4h-BTC trend follower
   is real edge or selection. If holdout composite is meaningfully positive
   (say > +0.5), this is the strongest single-asset crypto baseline I'd seen.

2. **The biggest unknown: w3 weakness.** WF window 3 has only 1 OOS trade for
   the champion. That's not a stable estimate. Either (a) BTC late-2025 was
   genuinely no-trade for this signal (good — strategy correctly stayed flat),
   or (b) the period has trends that 20/100 SMA misses. A diagnostic worth
   running OUTSIDE the iteration loop: examine when w3's signal goes long vs
   when it should have. Don't do this as an iteration — that's overfitting
   to a known-weak window.

3. **Multi-symbol redo, but properly.** The "basket" iteration failed because
   each symbol got the same BTC-tuned params. A better next direction is a
   **cross-sectional momentum** strategy (rank universe by N-day return,
   long top-decile equally weighted). That's a fundamentally different
   strategy spawn — `cp -r strategies/sma_cross strategies/xs_momentum`.

4. **Funding-aware sizing.** Once funding parquets are complete for the
   universe, position sizing as `size × (1 + funding_signal)` could add a
   small structural edge for trend-followers (long when funding is moderate
   negative, scale down when funding is hot positive).

5. **Don't tune the parameters more.** I tried fast/slow/vol_lookback variants
   at small distances around the optimum. None were KEPT. Diminishing returns
   here — the next iteration session should focus on structurally new
   hypotheses (different signal family, multi-asset frame, or new filter
   classes) rather than trying to squeeze more from 20/100/21.

## 5. Notes about the framework

**Smooth experience overall.** The keep/revert + audit loop did its job —
caught nothing this session because all my edits used `.shift(1)` from the
start, but it's reassuring. Tear sheets and history.jsonl made debugging easy.

**Minor framework rough edges**:

- **`runner.iterate` printed verdict JSON only at the end** — fine, but on a
  300s walk-forward run with no progress logging, it's hard to tell if it's
  hung. A simple `wf window i/4 done` log line would help.

- **`vol_lookback` parameter renaming**: I scaled it from 168 (1h-bars) to
  42/21/12 (4h-bars) when switching TF, and added a `bars_per_day` param.
  This sort of TF-change creates a multi-edit (logically) — the harness
  treats it as one diff but the param change AND the constant change AND
  the TF change all land together. No way around it within the "one change
  per iteration" rule, but maybe `bars_per_day` should be inferred from
  `DEFAULT_TF` automatically (the harness already knows the TF).

- **`history.jsonl` lines are ~5 KB each** with full WF window metrics
  embedded. Reading the file with anything but a script is painful.
  Consider a `--summary` flag that prints a slim view (iter, verdict,
  composite, note).

- **No bug** but a guideline gap: the docs say "edit only `strategy.py`",
  but `program.md` is also editable per the task brief. Worth clarifying
  in CLAUDE.md.

That's it. The framework is doing what it's supposed to: aggressively rejecting
my bad ideas (15 REVERTs out of 19), preserving the good ones, and showing me
the cumulative selection-bias tax via DSR.
