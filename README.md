# researchlab

Crypto strategy auto-research — adaptation of [karpathy/autoresearch](https://github.com/karpathy/autoresearch) to algorithmic trading on crypto perp futures.

## Idea

One file per strategy (`strategies/<name>/strategy.py`) is the *only* file an LLM agent edits. A fixed harness backtests it on Bybit USDT-perp 1m data, computes an out-of-sample composite score (`OOS_Sharpe − 0.5·MaxDD − penalties`), and a runner loop keeps or reverts the change. Run for hours. Read `runs/best.json`.

## Layout

- `data/` — OHLCV parquet, partitioned by symbol/month
- `datafeed/` — downloader + loader for Bybit perp
- `harness/` — backtest engine, metrics, splits, costs (do not let the agent edit)
- `strategies/<name>/` — `strategy.py`, `program.md`, `runs/`
- `runner/` — main loop + judge

## Quick start

```bash
# install uv once (https://astral.sh/uv) then:
uv sync                                   # creates .venv, installs deps from pyproject.toml
uv run python -m datafeed.download_bybit --symbol BTCUSDT --start 2025-01 --end 2025-01
uv run python -m harness.backtest strategies/ema_pilot --period 2025-01-01:2025-02-01
uv run python -m runner.iterate strategies/ema_pilot --start 2025-01-01 --end 2025-02-01 --note "first try"
```

Data is fetched via [ccxt](https://github.com/ccxt/ccxt) (`bybit` linear swap, 1m).

Web dashboard:

```bash
# dev (two terminals): hot-reload frontend + FastAPI
uv run uvicorn web.app:app --port 8000              # backend
cd frontend && npm install && npm run dev           # frontend on :5173 (proxies /api to :8000)

# prod (one process): FastAPI serves the built bundle
cd frontend && npm run build                        # outputs frontend/dist/
uv run uvicorn web.app:app --port 8000              # http://localhost:8000/
```

**New here?** [`GETTING_STARTED.md`](GETTING_STARTED.md) walks from
fresh-clone to running an LLM-driven research loop with Claude Code.

LLM agents: read [`AGENTS.md`](AGENTS.md) before touching anything. For a catalog of concrete strategy-improvement techniques, see [`METHODS.md`](METHODS.md).

## `lab/` — manual research

[`lab/`](lab/README.md) is the hand-driven research track, **separate from the
`runner.iterate` auto-loop**. Here we explore data, plot, and test hypotheses
with Claude one honest step at a time — no "one change per iter" rule and no
keep/revert verdict machine — but the scientific discipline (baseline, pooling,
OOS on a *different* period, no-lookahead, mind ~0.1–0.2% costs) still holds,
and the engine enforces the parts that can be enforced mechanically.

The engine exists because three research lines died to lookahead that entered
through *selection* — the anchor of an event, the size of a position, an inner
join — rather than through a missing `.shift()`. So an `lab.EventBook` makes
every column declare when it becomes known, and refuses to filter on one that
is only known afterwards. See [`lab/README.md`](lab/README.md) for the
mechanics and [`lab/PLAYBOOK.md`](lab/PLAYBOOK.md) for how we work.

Each line is an independent research thread in `lab/lines/<name>/` with its own
`README.md` journal, `_build_*.py` builders, and notebooks. Eleven earlier lines
are archived read-only in
[`notebooks/_archive/`](notebooks/_archive/README.md) — go there for what has
already been ruled out.

### Running notebooks

```bash
uv sync                              # dev deps (jupyterlab/ipykernel/nbconvert)
uv run jupyter lab                   # interactive UI in the browser (for a human)
uv run python -m lab new <line>      # scaffold a new research line
uv run python -m lab audit <book>    # check an event book and its manifest
uv run python -m pytest lab/tests -q # regressions on the engine itself
```

Headless execution (no UI — how Claude runs a notebook):

```bash
uv run jupyter nbconvert --to notebook --execute --inplace lab/lines/<line>/<nb>.ipynb
```

Plots: end a cell with `show("name")` instead of `plt.show()` — it renders
inline *and* saves `_out/name.png`, which Claude reads as an image (the
base64-PNG embedded in `.ipynb` is too large to read directly). `_out/` and
`.ipynb_checkpoints/` are git-ignored.

## Known limitations

These are real and documented; numbers will be biased upward by them. They
will be addressed when justified by the cost/benefit but do not block
research today.

### Survivorship bias in the symbol universe
The downloader filters Bybit USDT-perp instruments by their *current*
listing status; symbols that delisted before today are absent from the
universe entirely. On a 2024–2026 backtest, this means the only "alts"
present are the ones that **survived** that period — picking from
known-good outcomes biases cross-sectional and portfolio strategies
upward (literature suggests +0.2–0.5 Sharpe on multi-year crypto
backtests, depending on universe breadth).

Single-symbol BTC strategies are unaffected. Multi-symbol strategies
that include all available perps are. Treat OOS Sharpe gains from
adding "obscure" alts with skepticism until we snapshot historical
listings.

Mitigation when actually needed: maintain a monthly snapshot of
`/v5/market/instruments-info` and include each symbol only over its
active lifetime. Out of scope for the current pilot.

### Single shared cash book under cash_sharing + group_by
The harness wires `vectorbt.Portfolio.from_orders(cash_sharing=True,
group_by=True, call_seq="auto")`. This means all symbols draw from one
cash pool and vbt picks an internal order-execution sequence at each
bar. Fee allocation between legs becomes opaque — useful for
single-symbol pilots, less faithful for a multi-symbol live account
where cash availability per symbol is queue-position-dependent.

For now: documented, fine for single-symbol pilots. Expansion to
event-driven (backtrader / nautilus) for proper multi-symbol cash
queue is on the M6 backlog.

### Web dashboard — single-user, localhost only
`web/app.py` runs background subprocess jobs in daemon threads. Killing
the FastAPI process orphans them; jobs in flight lose their tail buffer.
There is no auth, no rate limiting, no remote authorization. Bind only
to `127.0.0.1` in production-grade scenarios. The dashboard is meant as
a local research console, not a multi-user service.

### Data path & reproducibility
Set `RESEARCHLAB_DATA_ROOT` if your parquet tree lives outside the repo
(e.g. on a separate fast disk). Each accepted iter records its env in
`runs/best.json` (python / package versions / git SHA / dataset
snapshot mtime); see `harness/env.py`. Cold reproduction requires
recreating that env.

## Strategy contract

Each `strategy.py` exports:

```python
DEFAULT_PARAMS: dict
PARAM_SPACE: dict   # hints, e.g. {"fast": (4, 200), "slow": (10, 500)}

def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    # returns long-format DataFrame: [timestamp, symbol, position] with position in [-1, 1]
    ...
```

Harness handles fees, slippage, sizing, metrics. Strategy only decides *what* to hold and *when*.
