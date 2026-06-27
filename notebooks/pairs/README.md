# notebooks/pairs — статистический арбитраж пар (manual research)

Ручное исследование парного арбитража вместе с Claude, в стороне от авто-цикла
`runner.iterate`. Свободно смотрим данные, рисуем, считаем — без правил «один
change за раз» и без verdict-машины. Дисциплина науки (baseline, pooling, OOS на
**другом** периоде, no-lookahead) — обязательна; см. `../pump/HOW_WE_WORK.md`.

## Запуск Jupyter (для человека)

```bash
uv run jupyter lab        # откроет UI в браузере
```

## Цикл «Claude пишет → выполняет → видит результат»

1. Claude правит ячейки (`NotebookEdit`) или создаёт `.ipynb`.
2. Выполнение без UI:
   ```bash
   uv run jupyter nbconvert --to notebook --execute --inplace notebooks/pairs/<nb>.ipynb
   ```
3. **Текст и таблицы** Claude читает прямо из `.ipynb` (`Read`).
4. **Графики**: в конце ячейки вызывай `show("имя")` вместо `plt.show()` —
   рисует inline (для тебя) и сохраняет `_out/имя.png`, который Claude читает
   как картинку.

## Хелпер (`_lab.py`)

```python
import sys; sys.path.insert(0, ".")   # cwd = notebooks/pairs
from _lab import *

list_symbols()                              # все символы на диске
coverage("AVAXUSDT")                        # ('2024-01','2026-04', 28)

# выровненная пара (inner-join по общему индексу), колонки a / b
px = load_pair("ETHUSDT", "BTCUSDT", "2024-01-01", "2024-07-01", tf="1h")

s   = log_spread(px)                         # log(a) - beta*log(b), beta по OLS
z   = zscore(s, window=24*7)                 # rolling causal z-score
hl  = half_life(s)                           # half-life возврата к среднему, в барах
show("eth_btc_spread")
```

`_lab.py` — обёртка над `datafeed.loader` + pair-хелперы. Данные — Bybit perp 1m
parquet; `tf` ресемплит на лету (`1h`, `4h`, `1d`, ...). Для парного анализа
по умолчанию берём `1h`/`4h`, 1m — только когда реально нужна гранулярность.

## ⚠️ No-lookahead для пар (читать перед первым тестом)

- **β и mean/std спреда оценивай на TRAIN, применяй вперёд.** `log_spread()` и
  `zscore()` без `window` используют весь переданный кусок — это только для
  глаз. Для теста: фитим на train-окне, торгуем на следующем.
- **Rolling z (`window=...`) — каузальный**, на каждом баре только прошлое.
- **Коинтеграция тоже может развалиться OOS.** Пара, коинтегрированная в 2024,
  не обязана быть такой в 2025 — проверяй на другом периоде.
- **Costs.** Парная сделка = 2 ноги → удвоенный round-trip (~0.2–0.4% alt-perp)
  плюс funding на обе ноги. Edge ниже этого — не edge.

## Окружение

`jupyterlab` / `ipykernel` / `nbconvert` — в dev-группе. `_out/` и
`.ipynb_checkpoints/` — в `.gitignore`.
