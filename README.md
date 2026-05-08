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

LLM agents: read [`AGENTS.md`](AGENTS.md) before touching anything.

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
