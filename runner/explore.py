"""Quantitative EDA runner — run a research tool over the TRAIN-ONLY slice.

What this is
------------
The disciplined alternative to "guess a param, run runner.iterate, repeat".
Before forming a hypothesis, *measure* the data: volatility regimes, funding
relationships, autocorrelation, whatever a registered research tool exposes.
The result is structured (summary + scalar metrics) so you quote it in
program.md and form a hypothesis that makes a prediction BEFORE you backtest.

The discipline (why this is safe)
---------------------------------
Every tool receives a ``TrainData`` handle that loads bars only inside the
train slice ``[2024-01-01, ~2025-07)`` — the same boundary runner.optimize
uses, hard-capped before the holdout. EDA can therefore never see the OOS tail
that runner.iterate judges with, nor the holdout. Measuring on train and
letting OOS judge blind is the rule (AGENTS.md §2, METHODS.md §6.2): it keeps
you from overfitting the *choice of what to test* to the data that scores you.

Agent workflow
--------------
    1. runner.explore --list                              # what tools exist?
    2. runner.explore strategies/<name> --tool vol_regime_split
                                                          # measure on train
    3. Write the finding + a falsifiable hypothesis into program.md.
    4. Edit strategy.py for that ONE hypothesis.
    5. runner.iterate strategies/<name> --note "..."      # OOS judges it blind.

Writing your own tool (becomes reusable for every future agent)
---------------------------------------------------------------
Drop a file in ``strategies/<name>/research/`` that defines a function with the
contract signature and decorates it::

    from harness.research import research_tool, ToolMeta, ResearchResult

    @research_tool(ToolMeta(name="my_probe", question="...", tags=["..."]))
    def my_probe(data, lookback: int = 30) -> ResearchResult:
        rets = data.returns()                 # train-only, already clipped
        return ResearchResult(summary="...", metrics={"x": 1.23})

runner.explore auto-imports that directory, so ``--tool my_probe`` just works.
If it proves broadly useful, the operator promotes it into
``harness/research/lib/`` so it appears in --list for every strategy.

Usage
-----
    uv run python -m runner.explore --list
    uv run python -m runner.explore strategies/pivot_cci --tool funding_corr
    uv run python -m runner.explore strategies/zret_mr_alts --tool vol_regime_split \
        --param vol_window=48 --param n_regimes=4 --symbol ETHUSDT
    uv run python -m runner.explore --write-registry   # (operator) refresh REGISTRY.md
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import io
import json
import sys
from pathlib import Path

# Importing the package registers all vetted tools.
import harness.research as research
from harness.research import REGISTRY, load_train_data


def _import_scratch_tools(strategy_dir: Path) -> list[str]:
    """Import strategies/<name>/research/*.py so their @research_tool's register.

    Returns a list of human-readable load errors (empty if all clean).
    """
    errors: list[str] = []
    research_dir = strategy_dir / "research"
    if not research_dir.is_dir():
        return errors
    for f in sorted(research_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_name = f"scratch_research_{strategy_dir.name}_{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001 — surface, don't crash the runner
            errors.append(f"{f.name}: {type(e).__name__}: {e}")
    return errors


def _registry_rows() -> list[dict]:
    rows = []
    for name, tool in sorted(REGISTRY.items()):
        m = tool.meta
        src = Path(inspect.getsourcefile(tool.fn) or "")
        scope = "scratch" if "strategies" in src.parts else "lib"
        rows.append({"name": name, "question": m.question,
                     "tags": m.tags, "params": m.params,
                     "returns": m.returns, "scope": scope})
    return rows


def _print_list() -> None:
    rows = _registry_rows()
    if not rows:
        print("[explore] no tools registered.")
        return
    print(f"[explore] {len(rows)} research tool(s):\n")
    for r in rows:
        tags = f"  ({', '.join(r['tags'])})" if r["tags"] else ""
        print(f"  {r['name']}  [{r['scope']}]{tags}")
        print(f"      {r['question']}")
        if r["params"]:
            for k, v in r["params"].items():
                print(f"        --param {k}=…   {v}")
    print("\nrun:  runner.explore strategies/<name> --tool <name> [--param k=v ...]")


def _registry_markdown() -> str:
    rows = _registry_rows()
    out = ["# Research-tool registry",
           "",
           "_Auto-generated by `runner.explore --write-registry`. Do not edit by hand._",
           "",
           "Run a tool: `uv run python -m runner.explore strategies/<name> --tool <name>`.",
           "",
           "| tool | scope | question | tags |",
           "|------|-------|----------|------|"]
    for r in rows:
        out.append(f"| `{r['name']}` | {r['scope']} | {r['question']} | "
                   f"{', '.join(r['tags'])} |")
    return "\n".join(out) + "\n"


def _coerce_params(fn, raw: dict) -> dict:
    """Coerce --param strings against the tool's typed defaults."""
    sig = inspect.signature(fn)
    out = {}
    for k, v in raw.items():
        if k not in sig.parameters:
            raise SystemExit(f"--param {k}: tool '{fn.__name__}' has no such parameter")
        default = sig.parameters[k].default
        if isinstance(default, bool):
            out[k] = str(v).lower() in ("1", "true", "yes", "y")
        elif isinstance(default, int) and not isinstance(default, bool):
            out[k] = int(v)
        elif isinstance(default, float):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def main() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("strategy_dir", nargs="?", default=None,
                    help="Path to strategies/<name>/ (sets symbols/tf and "
                         "auto-loads its research/ scratch tools).")
    ap.add_argument("--tool", help="Name of the research tool to run.")
    ap.add_argument("--list", action="store_true", help="List registered tools and exit.")
    ap.add_argument("--param", action="append", default=[], metavar="k=v",
                    help="Tool parameter (repeatable). Coerced to the tool's default type.")
    ap.add_argument("--symbol", default=None,
                    help="Override symbol (shortcut for --param symbol=...).")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="Override the symbol set for TrainData (default: strategy's).")
    ap.add_argument("--tf", default=None, help="Override timeframe (default: strategy's).")
    ap.add_argument("--start", default=research.loader.DEFAULT_PERIOD_START)
    ap.add_argument("--end", default=research.loader.DEFAULT_PERIOD_END)
    ap.add_argument("--oos-fraction", type=float, default=0.25)
    ap.add_argument("--write-registry", action="store_true",
                    help="(Operator) regenerate harness/research/REGISTRY.md and exit.")
    args = ap.parse_args()

    # Load scratch tools first so --list / --write-registry see them too.
    scratch_errors: list[str] = []
    if args.strategy_dir:
        scratch_errors = _import_scratch_tools(Path(args.strategy_dir).resolve())
        for e in scratch_errors:
            print(f"[explore] scratch tool failed to load: {e}", flush=True)

    if args.write_registry:
        target = Path(research.__file__).resolve().parent / "REGISTRY.md"
        target.write_text(_registry_markdown(), encoding="utf-8")
        print(f"[explore] wrote {target} ({len(REGISTRY)} tools)")
        return

    if args.list or not args.tool:
        _print_list()
        if not args.tool and not args.list:
            print("\n[explore] no --tool given; nothing to run.")
        return

    if args.tool not in REGISTRY:
        raise SystemExit(f"unknown tool '{args.tool}'. Run --list to see available tools.")
    tool = REGISTRY[args.tool]

    raw = {}
    for p in args.param:
        if "=" not in p:
            raise SystemExit(f"--param must be k=v, got '{p}'")
        k, v = p.split("=", 1)
        raw[k] = v
    if args.symbol is not None:
        raw.setdefault("symbol", args.symbol)
    kwargs = _coerce_params(tool.fn, raw)

    data = load_train_data(
        strategy_dir=args.strategy_dir,
        symbols=args.symbols, tf=args.tf,
        period_start=args.start, period_end=args.end,
        oos_fraction=args.oos_fraction,
    )
    s, c = data.window
    print(f"[explore] tool={args.tool}  symbols={data.symbols}  tf={data.tf}", flush=True)
    print(f"[explore] train-only window: {s.date()} -> {c.date()} "
          f"(OOS tail and holdout NOT loaded)", flush=True)

    result = tool.fn(data, **kwargs)

    print(f"\n[explore] {result.summary}\n", flush=True)
    print(json.dumps({
        "tool": args.tool,
        "strategy": Path(args.strategy_dir).name if args.strategy_dir else None,
        "symbols": data.symbols,
        "tf": data.tf,
        "train_window": [str(s), str(c)],
        "params": kwargs,
        "result": result.to_dict(),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
