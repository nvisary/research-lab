# donchian_atr_trend

## Thesis (reframed)
Original thesis (donchian breakout = trend onset) failed: trend-following bled
across windows. New thesis: in non-trending regimes, donchian-N pierces are
exhaustion points, not breakouts → fade them back to channel mid.

## Logic (current best — iter 4)
- Decision TF: 4h on BTC+ETH.
- Donchian-20 high/low. Channel mid = (upper+lower)/2.
- Chop gate: |daily EMA(100) slope over 12 bars| < 5%.
- Fade short: prior-bar high pierced upper, current close back inside.
- Fade long: mirror at lower.
- Exit: close back at mid, ATR(14)·2.5 stop, 24-bar timeout.

## Iter history
| iter | verdict | composite | note |
|------|---------|-----------|------|
| 1 | KEEP (baseline) | -2.83 | donchian breakout trend-follower |
| 2 | REVERT | -4.05 | long-only + slope filter — too few trades |
| 3 | REVERT | -5.36 | shorter donchian + SOL — bear-trades hurt |
| 4 | KEEP | **+0.054** | invert to FADE in chop-only regime |

## What's been ruled out
- Donchian breakout as trend-onset (Q3'24-Q2'25 crypto regime hostile).
- Long-only trend-follower with strong slope filter (sample collapse).
- Multi-symbol with SOL.
