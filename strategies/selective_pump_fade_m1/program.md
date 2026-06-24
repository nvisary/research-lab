# selective_pump_fade_m1

## Thesis

Use the `pump_dump_m1` champion as an event detector, but treat it as a
research signal rather than a finished strategy. The first testable extension is
symbol selectivity: fade one-minute pump shocks only on symbols whose accepted
ledger showed positive aggregate short-fade behavior, and keep dump-reclaim
longs only on symbols where that auxiliary branch did not look structurally
weak.

## Baseline

- Decision timeframe: 1 minute.
- Core event detector: same abnormal one-minute return plus abnormal volume
  shock definition as `pump_dump_m1`.
- Main trade: short selected pump shocks after a one-bar delay.
- Auxiliary trade: long selected dump-reclaim events after confirmation.
- Exit: fixed 50 minute hold, inherited from the champion because 40m and 60m
  both lost in walk-forward tests.
- Sizing: raw total-equity sizing, small fixed fraction per active event.

## First Questions

- Does symbol selectivity improve walk-forward composite without collapsing
  activity further?
- Does the short-fade edge survive without BTC/ETH/SOL/DOGE pump shorts?
- Are the selected extra symbols broad enough to avoid single-trade dependence?
- Does the auxiliary long-reclaim branch still help after removing weak symbols?

## Rules

- No holdout during iteration.
- Do not use external files or future data inside `generate_signals`.
- Keep final positions dependent only on completed prior bars.
- Prefer one structural change per iteration.
