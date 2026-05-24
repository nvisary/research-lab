import json
import random
import re
import subprocess
from pathlib import Path


def tune_strategy(strategy_name, param_space, n_iterations=10, note="tuning"):
    strategy_path = Path(f"strategies/{strategy_name}/strategy.py")
    if not strategy_path.exists():
        print(f"Strategy path {strategy_path} does not exist.")
        return

    print(f"--- Starting tuning for {strategy_name} ({n_iterations} iterations) ---")

    for i in range(n_iterations):
        print(f"--- Iteration {i + 1} ---")

        # Sample params
        params = {}
        for k, v in param_space.items():
            if isinstance(v, list):
                params[k] = random.choice(v)
            elif isinstance(v[0], int) and isinstance(v[1], int):
                params[k] = random.randint(v[0], v[1])
            else:
                params[k] = random.uniform(v[0], v[1])
        print("Sampled params:", params)

        with open(strategy_path, "r") as f:
            content = f.read()

        match = re.search(
            r"(DEFAULT_PARAMS:\s*dict\s*=\s*\{)(.*?)(^\})",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            print("Could not find DEFAULT_PARAMS block")
            continue

        block = match.group(2)
        for k, v in params.items():
            if isinstance(v, float):
                block = re.sub(rf'"{k}":\s*[0-9\.]+,', f'"{k}": {v:.5f},', block)
            elif isinstance(v, int):
                block = re.sub(rf'"{k}":\s*[0-9]+,', f'"{k}": {v},', block)
            else:
                block = re.sub(rf'"{k}":\s*.*?,', f'"{k}": {v},', block)

        new_content = content[: match.start(2)] + block + content[match.end(2) :]

        with open(strategy_path, "w") as f:
            f.write(new_content)

        # Run iterate
        result = subprocess.run(
            [
                ".venv\\Scripts\\python",
                "-m",
                "runner.iterate",
                f"strategies/{strategy_name}",
                "--note",
                note,
            ],
            capture_output=True,
            text=True,
        )

        out = result.stdout
        try:
            start_idx = out.find("{")
            if start_idx != -1:
                json_str = out[start_idx:]
                data = json.loads(json_str)
                print(
                    f"Verdict: {data.get('verdict')} | Composite: {data.get('composite')} | OOS Sharpe: {data.get('oos_sharpe')} | Trades: {data.get('oos_n_trades')}"
                )
            else:
                print("Could not find JSON in output")
        except Exception as e:
            print(f"Could not parse JSON: {e}")


if __name__ == "__main__":
    # Proposal 3: Tune ADX Filter
    ADX_SPACE = {
        "adx_period": (10, 25),
        "adx_max": (15, 35),
    }
    tune_strategy(
        "fib_mr_channels", ADX_SPACE, n_iterations=10, note="prop3: tuning ADX filter"
    )
