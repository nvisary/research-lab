import json
import random
import re
import subprocess

PARAM_SPACE = {
    "z_period": (20, 100),
    "z_threshold": (1.5, 3.0),
    "ema_period": (100, 400),
    "slope_threshold": (0.0, 0.005),
    "cci_period": (10, 50),
    "cci_threshold": (50, 150),
    "rsi_period": (7, 21),
    "rsi_threshold": (20, 50),
    "use_cci": [0, 1],
    "use_rsi": [0, 1],
    "long_only": [0, 1],
}

strategy_path = "strategies/pivot_cci/strategy.py"

for i in range(30):
    print(f"--- Iteration {i + 1} ---")
    params = {}
    for k, v in PARAM_SPACE.items():
        if isinstance(v, list):
            params[k] = random.choice(v)
        elif isinstance(v[0], int) and isinstance(v[1], int):
            params[k] = random.randint(v[0], v[1])
        else:
            params[k] = random.uniform(v[0], v[1])

    with open(strategy_path, "r") as f:
        content = f.read()
    match = re.search(
        r"(DEFAULT_PARAMS:\s*dict\s*=\s*\{)(.*?)(^\})",
        content,
        re.MULTILINE | re.DOTALL,
    )
    block = match.group(2)
    for k, v in params.items():
        if isinstance(v, float):
            block = re.sub(rf'"{k}":\s*[0-9\.]+', f'"{k}": {v:.5f}', block)
        else:
            block = re.sub(rf'"{k}":\s*[0-9]+', f'"{k}": {v}', block)
    with open(strategy_path, "w") as f:
        f.write(content[: match.start(2)] + block + content[match.end(2) :])

    result = subprocess.run(
        [
            ".venv\\Scripts\\python",
            "-m",
            "runner.iterate",
            "strategies/pivot_cci",
            "--note",
            "tuning",
        ],
        capture_output=True,
        text=True,
    )
    out = result.stdout
    try:
        data = json.loads(out[out.find("{") :])
        print(
            f"Verdict: {data.get('verdict')} | Composite: {data.get('composite')} | OOS Sharpe: {data.get('oos_sharpe')} | Trades: {data.get('oos_n_trades')}"
        )
    except:
        print("Error parsing result.")
