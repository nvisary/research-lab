# mom_tsmom — time-series momentum (per asset)

## Baseline

Per symbol on 1d bars: sign of 14-day trailing return is the
position direction. Long if return > 0, short if return < 0,
flat at exact zero. Each asset evaluated independently across the
10-major basket.

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1d"
lookback        = 14
long_only       = 0
```

## Hypothesis

Past N-day return predicts the sign of the next N-day return. The
classic TSM result (Moskowitz, Ooi, Pedersen 2012) extended to
crypto majors. Long-bias of crypto historical drift means TSM
should pick up steady up-trends in BTC/ETH and ride flips in alts.

## Why this slot in the quadrant

Time-series momentum. Per-asset, absolute direction. Orthogonal
to mom_xsection (which is rank-based) — TSM fires when the WHOLE
basket trends one direction, while CSM fires on dispersion.

In a uniform bull market, TSM is fully long all 10 symbols; CSM is
balanced 30/30 long-short. In a chopping market with idiosyncratic
movers (e.g. one alt rallies on its own), TSM may be flat while
CSM longs the rallying alt and shorts the laggards.

## Open questions

- Lookback sweep — 7 / 14 / 30 / 60 / 90 days
- Vol-targeted sizing: position = sign(ret) × clip(target_vol / asset_vol, 0, 2)
- Skip latest 1 day (anti-microstructure)
- Asymmetric thresholds (e.g. require |ret| > 5% to enter) — reduces churn
- Funding-aware: shorts cost funding in long-bias regimes
