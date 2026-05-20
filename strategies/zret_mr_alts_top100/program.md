# zret_mr_alts_top100 — research log

## Slot
Same architecture as `zret_mr_alts` (return-z MR per-symbol with 4h regime gate
and vol-floor). **Only structural difference**: universe expanded from 24
mid-caps (excluding top-5 by mcap) to top-100 by quote volume 2024-2025.

## Why
1. zret_mr_alts on 24 alts failed holdout (sharpe -0.52 on 2026-Q1+Apr).
   Maybe the signal needs more parallel symbols.
2. More symbols → more parallel trades per bar → tighter per-window Sharpe
   estimates → BHY haircut shrinks (more statistical power per trial).
3. User asked for top-50/100 — explicit broader basket.

## Universe (committed)
100 symbols ranked by total quote volume 2024-01..2026-01, coverage ≥ 95% of
hourly bars. INCLUDES top-5 (BTC/ETH/SOL/BNB/XRP) this time. Top 10 by volume:
BTC, ETH, XRP, DOGE, SUI, LINK, AVAX, WLD, NEAR, BNB. Saved to `universe.json`.

## Caveats
- **Survivorship bias** (same as smaller universe): currently-listed Bybit perps.
  Symbols delisted during 2024-2026 missing.
- **Mixed-cap basket**: BTC has very different microstructure from low-volume
  mid-caps. Aggregating into one MR signal may be too coarse.
- **Compute**: 100 symbols × 24 months × 15min ≈ 7M bars total. Each iter
  takes longer than zret_mr_alts.

## Best params inherited
From zret_mr_alts iter 11 (composite +0.26 in-sample, but holdout -0.52):
- z_window=96, entry_k=2.0, exit_k=1.0
- regime_quantile=0.2, vol_floor_q=0.65
- symmetric L/S

## Iteration log

| # | Verdict | Composite | OOS Sharpe | Stitched-OOS | DSR | Note |
|---|---------|-----------|------------|--------------|----|------|
| — | — | — | — | — | — | (pending baseline) |
