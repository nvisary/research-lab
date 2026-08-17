# notebooks/ — manual research

Ручное исследование гипотез вместе с Claude, в стороне от авто-цикла
`runner.iterate`. Здесь можно свободно смотреть данные, рисовать, считать —
без правил «один change за раз» и без verdict-машины.

## Запуск Jupyter (для человека)

```bash
uv run jupyter lab        # откроет UI в браузере
```

## Цикл «Claude пишет → выполняет → видит результат»

1. Claude правит ячейки (`NotebookEdit`) или создаёт `.ipynb`.
2. Выполнение без UI:
   ```bash
   uv run jupyter nbconvert --to notebook --execute --inplace notebooks/<nb>.ipynb
   ```
3. **Текст и таблицы** Claude читает прямо из `.ipynb` (`Read`).
4. **Графики**: в конце ячейки вызывай `show("имя")` вместо `plt.show()` —
   функция и рисует inline (для тебя), и сохраняет `_out/имя.png`,
   который Claude читает как картинку. (base64-PNG внутри `.ipynb`
   слишком большой, чтобы Claude его прочитал напрямую.)

## Хелпер

```python
import sys; sys.path.insert(0, '..')
from notebooks._lab import *

list_symbols()                      # все 173 символа на диске
coverage('AVAXUSDT')                # ('2024-01','2026-04', 28)
df = ohlcv('AVAXUSDT','2024-06-01','2024-07-01', tf='1h')
fr = funding('AVAXUSDT','2024-06-01','2024-07-01')
show("avax_close")                  # inline + _out/avax_close.png
```

`_lab.py` — тонкая обёртка над каноничным `datafeed.loader`. Данные —
bybit perp 1m parquet; `tf` ресемплит на лету (`1h`, `4h`, `1d`, ...).

## Окружение

`jupyterlab` / `ipykernel` / `nbconvert` стоят в dev-группе
(`uv add --group dev ...`). `_out/` и `.ipynb_checkpoints/` — в `.gitignore`.
