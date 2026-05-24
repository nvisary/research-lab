# bb_squeeze_trend

## Thesis
BB outer-band touch → mean reversion to BB mid. The breakout interpretation
(original) was an outright loser; reversion-to-mid in chop regime works.

## Logic (current best)
- Decision TF: 1h on BTC+ETH.
- Chop gate: |4h EMA slope| < 3% over last 12 bars.
- Long: prior-bar low pierced lower band AND close back inside lower band, in chop.
- Short: mirror at upper band.
- Exit: close back at BB mid, ATR(14)·3 stop, 36-bar timeout.

## Iter history
| iter | verdict | composite | note |
|------|---------|-----------|------|
| 1 | KEEP (baseline) | -6.94 | BB-squeeze BREAKOUT + 4h trend gate — wrong hypothesis |
| 2 | KEEP | -2.81 | switch to MR: outer-band touch → revert to mid, with-trend filter |
| 3 | KEEP | **+0.014** | chop-only gate (|4h slope|<3%) — same pattern that fixed vwap |

## Ruled out
- BB squeeze + breakout direction (iter 1) — fakes dominate in this universe.
- MR with trend-direction filter (iter 2) — trending regimes are momentum, not MR.

## Caveats
- Single window (W3) dominates Sharpe — fragile.
- Stitched 24-mo PnL still negative; positive composite is WF-OOS slice luck.
- Real edge lives in flat-regime buckets (v1-v3 flat, Sharpe +3.6 to +7.7).
