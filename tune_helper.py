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
                # Match both "key": value, and "key": value
                block = re.sub(rf'"{k}":\s*[0-9\.]+,', f'"{k}": {v:.5f},', block)
            elif isinstance(v, int):
                block = re.sub(rf'"{k}":\s*[0-9]+,', f'"{k}": {v},', block)
            else:
                # Fallback
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
            # Find the JSON block (it might have trailing text)
            start_idx = out.find("{")
            if start_idx != -1:
                json_str = out[start_idx:]
                # Sometimes there's more output after the JSON, so we try to find the end
                # But runner.iterate usually prints JSON last or only.
                data = json.loads(json_str)
                print(
                    f"Verdict: {data.get('verdict')} | Composite: {data.get('composite')} | OOS Sharpe: {data.get('oos_sharpe')} | Trades: {data.get('oos_n_trades')}"
                )
            else:
                print("Could not find JSON in output")
        except Exception as e:
            print(f"Could not parse JSON: {e}. Output tail:")
            print("\n".join(out.split("\n")[-10:]))


if __name__ == "__main__":
    # Example usage for the current strategy
    FIB_PARAM_SPACE = {
        "window": (20, 100),
        "sl_pct": (0.002, 0.02),
        "atr_period": (10, 30),
        "atr_k": (1.0, 4.0),
    }
    tune_strategy(
        "fib_mr_channels", FIB_PARAM_SPACE, n_iterations=10, note="random search tuning"
    )
