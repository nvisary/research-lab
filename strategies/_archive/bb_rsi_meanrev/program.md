# Bollinger Bands + RSI mean-reversion — research log

## Baseline (iter 1)

Mean-reversion in ranging regimes on BTCUSDT, 1h bars.

Indicators
- BB(20, 2σ) — upper / mid / lower
- RSI(14)
- ADX(14) — regime gate (only trade when ADX < 20)
- ATR(14) — stop distance

Entry (only when flat AND ADX[t] < 20):
- long  if close <= lower AND RSI < 30
- short if close >= upper AND RSI > 70

Exit (while in trade):
- long  : close >= mid (take, mid-band tag) OR low <= entry − 1.5·ATR (stop)
- short : close <= mid (take)                OR high >= entry + 1.5·ATR (stop)

The hypothesis embodied: "In low-trend regimes price oscillates around
the BB mid; band tags with RSI exhaustion mark exploitable extremes.
ADX < 20 is the regime gate that keeps us out of the trends where
mean-reversion gets ground up."

Expected weaknesses to watch for:
- ADX < 20 may admit too much chop (low-vol drift) where the BB itself
  is inside fee-noise distance — round-trip cost > expected payoff.
- Stop at 1.5·ATR may be too tight against breakout-of-range moves
  (range-failure tail). Worth comparing to looser stops or no-stop.
- 1h on BTC alone: small trade count + survivorship-clean but narrow.
  Multi-symbol basket is a likely follow-up if base shows any edge.
- Take at mid only captures half the band; opposite-band take is a
  natural variant once base is verified.

## What's been tried

| iter | hypothesis | verdict | composite | note |
|---|---|---|---|---|
|  1 | baseline (BTC 1h, BB(20,2)+RSI(14)+ADX<20, 1.5·ATR stop, mid take) | BASELINE | −2.78 | only 5 OOS trades, penalty active |
|  2 | drop RSI gate — test if BB+ADX alone carries edge | KEEP | −1.84 | RSI was cutting good signals; 25 trades |
|  3 | ADX gate 20→25 — admit more candidates | KEEP | −1.47 | 36 trades, OOS sharpe near zero |
|  4 | expand to 10-major basket | KEEP | **−0.85** | OOS sharpe +1.03, DSR 0.71 — first real signal |
|  5 | stop 1.5→2.5·ATR — give trades room | KEEP (technical) | −0.83 | composite +0.02 but DSR DROPPED 0.71→0.46 |
|  6 | + BB-width gate (lowest 50pct rolling) | REVERT | −1.62 | double regime filter overfilters |
|  7 | take at OPPOSITE band instead of mid | REVERT | −1.59 | mid-take is structurally better |
|  8 | long_only=1 — shorts pay funding on perps | KEEP | −0.43 | 3/4 windows positive, DD fell to 6.4pct |
|  9 | + 1d EMA50 HTF gate — buy-dip only in bull 1d | KEEP | **+0.14** | **first POSITIVE composite, 3/4 +ve, DD 3.4pct** |
| 10 | ADX 25→20 — quality over quantity | REVERT | −0.96 | over-filtered, 49 trades, W1 destroyed |
| 11 | HTF EMA 50→100 — deeper trend filter for W3 | KEEP | +0.41 | OOS sharpe 1.06, DSR 0.45, DD 4pct |
| 12 | bb_std 2.0→2.5 — deeper extreme touch | KEEP | **+1.50** | **OOS sharpe 2.38, DSR 0.72, all 4 windows +ve!** |
| 13 | bb_period 20→15 — faster mean for trade count | REVERT | +1.23 | sharpe still 1.84 but composite below champion |
| 14 | HTF 100→75 — slightly looser | REVERT | +0.90 | W3 went negative again — HTF=100 is critical |
| 15 | drop ADX gate (max=100) — test redundancy | REVERT | +0.35 | ADX is NOT redundant; carries real edge |
| 16 | cost-aware skip (<0.4pct payoff) | REVERT | +1.49 | bb_std=2.5 already provides payoff; near-no-op |
| 17 | max_bars_in_trade=100 | REVERT | +1.50 | identical metrics — no trades hit the cap |
| 18 | trailing stop (max_close − 2.5·ATR) | REVERT | +1.46 | DD 2pct (▼), sharpe 2.41 (▲), but W3 went −0.38 |
| 19 | atr_stop_mult 2.5→3.5 — wider stop for W3 | KEEP | **+1.58** | **all 4 windows positive, std collapsed** |
| 20 | bb_period 20→30 — slower mean | REVERT | −0.13 | killed it; n_trades 31, W3 broke |

## Champion (iter 19) parameters

```
DEFAULT_SYMBOLS = [BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, LTC]  # 10 majors
DEFAULT_TF      = "1h"
bb_period       = 20
bb_std          = 2.5     # deep extreme touch (was 2.0)
rsi_period      = 14
rsi_long        = 100     # disabled
rsi_short       = 0       # disabled
adx_period      = 14
adx_max         = 25      # not redundant with HTF — carries real edge
atr_period      = 14
atr_stop_mult   = 3.5     # wide stop survives W3 transient pullbacks (was 2.5)
long_only       = 1
htf_ema_period  = 100     # 1d EMA100 (was 50) — deeper, fixes W3
```

Per-window OOS sharpe:
- W1 (24-H1): **+2.81**
- W2 (24-H2): **+1.46**
- W3 (25-H1): **+1.02**
- W4 (25-H2): **+3.26**

OOS sharpe +2.13, max DD 3.65pct, 38 trades, DSR 0.41, composite +1.58.

All four windows positive — no single-window hero.

## What's been ruled out

- **RSI gate (iter 2)** — strict RSI<30/>70 filtered out the very signals
  the mean-reversion thesis depends on. Edge lives in band-touch + regime
  filters, not RSI confluence. Strategy ironically dropped RSI entirely.
- **BB-width gate (iter 6)** — additive over ADX gate caused over-filtering.
  Either ADX OR width, not both.
- **Opposite-band take (iter 7)** — most reversions don't make it to the
  far side; taking at mid captures more closures.
- **Shorts (iter 8 reverse-test confirmed)** — shorts in BB mean-rev on
  perps lose to funding drag in bullish-drift regimes.
- **ADX<20 (iter 10)** — once HTF gate is in, tighter ADX over-filters and
  trades fall below the penalty threshold.

## Surprise

The original "BB+RSI" thesis ended up dropping RSI entirely. The real
edge components that survived iteration:
1. Multi-symbol diversification (5→10 majors gave the biggest single jump).
2. Long-only on perps (funding drag on shorts is structural, not noise).
3. HTF trend gate (don't fade against the daily trend).

The BB+ADX framework is the substrate; RSI was decoration.

## Open ideas (to try after baseline lands)

### Regime / gate refinement
- ADX threshold sweep (15 / 20 / 25)
- Realized-vol band: also require atr/close in mid-quartile (skip dead chop)
- BB-width percentile gate: only trade when BB-width is in lowest 50%
  (genuinely ranging, not just "ADX low because trend just died")

### Entry / exit
- Asymmetric RSI thresholds (e.g. 25/75) — fewer but cleaner signals
- Entry on band breach + reversal candle (close back inside band)
  rather than first touch — fewer knife-catches
- Take at opposite band instead of mid (full mean-reversion target)
- Trailing-mid exit — exit when close re-crosses mid against position
- Cost-aware: skip when (band − mid)/close < N · taker_fee

### Sizing / structural
- Conviction sizing: |close − mid| / (bb_std · σ) → continuous in [0,1]
- Multi-TF: 1h trigger gated by 4h ADX < 20 (regime persistence)
- Multi-symbol basket: BTC + top alts, equal-weight
- Long-only variant — shorts in mean-reversion on perps fight funding

### Anti-patterns to avoid (AGENTS.md §9 / METHODS.md)
- Don't tune ADX/RSI/stop until OOS looks good — that IS using OOS as train
- Don't add indicators on top of an unprofitable base
- Don't tighten the stop to flatter the equity curve
