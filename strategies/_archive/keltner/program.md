# Keltner Channels research log

## Baseline (iter 1)

Pure Keltner-channels breakout on BTCUSDT, 4h bars, EMA(20) ± 2.0·ATR(10).
Long+short. No filters. No vol-targeted sizing. Single-symbol.
Position is +1 outside upper band, -1 outside lower band, 0 between.

The hypothesis embodied: "Volatility-adaptive bands signal trends — the
out-of-band move is the signal worth trading; inside the band, sit out."

This is the simplest possible Keltner baseline. The expected weakness
is regime sensitivity: in choppy markets the band is crossed often
(whipsaws); in strong trends entries arrive late and exits trigger on
the first pullback inside the band. Fixing both is the research agenda.

## What's been ruled out

- **Long-only on BTC alone (iter 3, REVERT)** — shorts weren't the problem;
  baseline longs themselves bleed in W1 and W4. The breakout has near-zero
  edge on BTC 4h outside a single regime (W2 mid-2024 rally).
- **Pure mean-reversion (iter 4, REVERT)** — flipping the sign helped W1 a
  bit (+0.83 vs −3.92) but blew up W3 and W4. The Keltner band is not a
  reliable fade signal on this asset/TF.
- **Vol-targeted sizing (iters 7 & 13, both REVERT)** — both single-symbol
  and multi-symbol. Reduced upside (W2) more than it reduced downside; net
  negative. The sharpe gain from cross-window equalisation didn't
  materialise. May need a dynamic vol target rather than fixed 2pct.
- **Long-only on multi-symbol basket (iter 9, REVERT)** — surprisingly,
  shorts in W3 must have been profitable; killing them dropped W3 from
  −1.01 to −3.43. Long-only is NOT a free win once diversified.
- **Wider multiplier (iter 14, REVERT, 2.0->2.5)** — fewer but cleaner
  trades didn't help: it cut the n_trades to 40 and ate the low-trades
  penalty. The default 2.0 is a reasonable trade frequency.
- **ADX gate (iter 5 KEEP, iter 11 ablation also KEEP)** — ADX>20 helped
  marginally early on but became redundant once 1d trend gate was
  added. Net signal: cheap to drop.
- **Same-TF SMA200 trend filter (iter 2 KEEP marginal, iter 12 ablation
  KEEP)** — once the 1d EMA gate is in place, the same-TF SMA200 is
  redundant and slightly hurts at trend reversal points.

## What's been tried (high-level)

| iter | hypothesis | verdict | composite | note |
|---|---|---|---|---|
|  1 | baseline (BTC 4h, EMA20 ± 2 ATR breakout long+short) | BASELINE | −3.58 | unstable across windows |
|  2 | + SMA200 trend regime filter | KEEP | −3.51 | marginal |
|  3 | long-only | REVERT | −4.42 | longs alone still bleed |
|  4 | mean-reversion variant | REVERT | n/a | filter killed all OOS trades |
|  5 | + ADX>20 gate | KEEP | −3.45 | marginal |
|  6 | + 1d EMA20 trend gate (resample_higher) | KEEP | −2.82 | helped W1 and W4 |
|  7 | + vol-target sizing (BTC only) | REVERT | −3.08 | scale-invariant for sharpe |
|  8 | multi-symbol BTC+ETH+SOL+BNB+XRP | KEEP | −1.76 | big jump, n_trades 32 |
|  9 | + long-only on basket | REVERT | −2.11 | shorts in W3 were valuable |
| 10 | expand basket to 10 majors | KEEP | −1.10 | OOS sharpe positive (+0.83) |
| 11 | drop ADX gate (redundant w/ 1d EMA) | KEEP | −1.03 | marginal |
| 12 | drop SMA200 trend filter (redundant) | KEEP | −0.89 | OOS sharpe +1.06 |
| 13 | + vol-target on basket | REVERT | −1.01 | cut upside more than down |
| 14 | wider multiplier 2.0->2.5 | REVERT | −1.03 | n_trades fell below penalty threshold |
| 15 | 1d EMA gate 20->50 | KEEP | **−0.52** | **best — OOS sharpe +1.33** |

## CPCV check on iter 15 champion

```
n_paths=45 (n_groups=10, k_test=2, embargo=1D)
median sharpe       : +0.45
IQR                 : [-0.07, +1.36]
pct paths positive  : 73.3 %
pct paths > 1.0     : 31.1 %
worst max_dd        : 20.1 %
```

Reading: the WF composite is inflated by one anomalous bull window (W2,
sharpe ~7). CPCV deflates the median to a still-positive +0.45 with most
paths positive. There IS a real edge but ~3x weaker than the WF picture
suggests, and tail DDs are deeper (20% worst path vs ~8% worst WF
window). The strategy is genuine but small-edge — not a champion, a
foothold.

## Champion (iter 15) parameters

```
DEFAULT_SYMBOLS = [BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, LTC]  # 10 majors
DEFAULT_TF      = "4h"
ema_period      = 20
atr_period      = 10
multiplier      = 2.0
long_only       = 0  # long+short
trend_ma        = 0  # disabled
adx_threshold   = 0  # disabled
htf_ema_period  = 50  # 1d EMA50 trend gate via resample_higher
```

## Open ideas (filter / sizing / structural)

### Regime / direction filters
- 200-period MA slope as a long/short permission gate
- ADX > 20 to confirm trending (avoid Keltner whipsaw in chop)
- Multi-TF confirmation: 4h trigger gated by 1d trend
- Funding-rate sign as long-bias filter (BTC perp pays ~+0.01% per cycle on average)
- Realized-vol band: skip when atr/close is in lowest quartile (no movement to capture)

### Structural / signal
- Mean-reversion variant: buy at lower, exit at middle (opposite to baseline)
- Asymmetric multiplier (looser long, tighter short — long-side has structural drift)
- Entry on band BREAK rather than band BREACH (require close > upper > prev-close)
- ATR-period adaptive to realized-vol regime
- Replace EMA centre with HMA / Kaufman AMA (less lag without overshoot)
- Smooth-stop: exit when close re-enters middle channel (not just opposite band)

### Sizing
- Volatility-targeted sizing (target=1.5% daily, scale by realized vol)
- Conviction sizing: |close - middle| / atr → continuous in [0, 1]
- Drawdown-aware shrink after rolling-N negative bars

### Multi-symbol (cautious)
- Cross-sectional Keltner rank — top-N alts breaking up vs bottom-N breaking down
- Beware survivorship bias on alt cocktails (documented in README)

### Cost-aware
- Skip bars where (atr/close × multiplier) < (taker_fee + slippage) × N — entries
  with edge thinner than the round-trip cost are guaranteed losers

### Anti-patterns to avoid (METHODS §9 / AGENTS.md)
- Adding indicators on top of unprofitable base
- Tightening stop-loss to flatter equity curve
- Cherry-picking parameters to OOS
