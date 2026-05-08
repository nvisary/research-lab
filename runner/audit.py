"""Standalone lookahead-bias audit CLI.

    uv run python -m runner.audit strategies/<name>
    uv run python -m runner.audit strategies/<name> --k 50 --bars 3000

Use this to test a strategy without running a full iter. Prints a structured
report and exits with code 0 on pass, 2 on lookahead, 3 on non-determinism.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datafeed.loader import load_many
from harness import backtest as bt
from harness import lookahead as la


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy_dir")
    ap.add_argument("--start", default="2024-01-01",
                    help="Start of the audit window (default: train start).")
    ap.add_argument("--days", type=int, default=120,
                    help="Length of the audit window in days.")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--k", type=int, default=12,
                    help="Number of per-bar perturbations.")
    ap.add_argument("--bars", type=int, default=1500,
                    help="Sample size in bars (last N).")
    args = ap.parse_args()

    strategy_dir = Path(args.strategy_dir).resolve()
    mod = bt.load_strategy(strategy_dir)
    symbols = (getattr(mod, "DEFAULT_SYMBOLS", None) or ["BTCUSDT"])[:2]

    start = pd.Timestamp(args.start, tz="UTC")
    end = start + pd.Timedelta(days=args.days)
    data = load_many(symbols, start, end, tf=args.tf)
    data = {s: df for s, df in data.items() if not df.empty}
    if not data:
        print(json.dumps({"error": "no data in audit window"}, indent=2))
        sys.exit(1)

    try:
        report = la.audit(mod, data, dict(getattr(mod, "DEFAULT_PARAMS", {})),
                          k=args.k, sample_bars=args.bars)
    except la.LookaheadError as e:
        print(json.dumps({
            "passed": False,
            "kind": "LookaheadError",
            "mode": e.mode,
            "message": str(e),
            "offending_first_5": [
                {"timestamp": str(t), "symbol": s, "orig": o, "perturbed": p}
                for (t, s, o, p) in (e.offending or [])[:5]
            ],
        }, indent=2, default=str))
        sys.exit(2)
    except la.DeterminismError as e:
        print(json.dumps({
            "passed": False, "kind": "DeterminismError", "message": str(e),
        }, indent=2, default=str))
        sys.exit(3)

    print(json.dumps({
        "passed": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy_dir.name,
        "symbols_tested": report.n_symbols_tested,
        "bars_tested": report.n_bars_tested,
        "perturbations": report.k_perturbations,
        "duration_seconds": round(report.duration_seconds, 2),
        "notes": report.notes,
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
