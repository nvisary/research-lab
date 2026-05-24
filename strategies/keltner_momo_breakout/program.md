# keltner_momo_breakout

## Thesis (reframed)
Original (breakout-momentum) thesis lost across windows. New thesis: in chop,
Keltner-band pierces are exhaustion — fade them to EMA. Final tuning showed
that requiring deeper pierce (k=2.5) on BTC-only gives more consistent
per-window signals than ETH/SOL inclusion.

## Logic (current best — iter 6)
- Decision TF: 1h on BTCUSDT only.
- Keltner: EMA(20) ± 2.5·ATR(20). Tighter than baseline (was 2.0).
- Chop gate: |4h EMA(100) slope over 12 bars| < 2%.
- Fade short: prior bar high > upper, close back inside.
- Fade long: mirror at lower.
- Exit: close at EMA(20), ATR(20)·2.5 stop, 24-bar timeout.

## Iter history
| iter | verdict | composite | note |
|------|---------|-----------|------|
| 1 | KEEP (baseline) | -4.56 | momentum breakout + ROC |
| 2 | KEEP | -4.02 | strict 2% slope filter — n_tr=1 catastrophe |
| 3 | KEEP | -3.84 | long-only momentum, simple trend filter |
| 4 | KEEP | -1.94 | invert to FADE in chop |
| 5 | KEEP | -1.91 | tighten chop slope 0.03→0.02, tighter stop |
| 6 | KEEP | **+0.414** | BTC-only + kelt_k 2.0→2.5 — variance collapsed |

## What's been ruled out
- Momentum breakout — trending hypothesis loses in Q4'24-Q2'25.
- Multi-symbol BTC+ETH (+SOL) — alts amplify window variance.
- Shallow Keltner pierces (k=2.0) — too many marginal signals.
