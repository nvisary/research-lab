"""Realtime microstructure polygon — a live viewer for Bybit order flow.

Subscribes to the same streams as ``microstructure.collector`` (reusing its
ccxt.pro watch loops) but, instead of writing to disk, computes rolling
microstructure metrics per symbol and renders them in a Textual dashboard.
Pass ``--record`` to also persist the session to parquet like the collector.

Run it::

    uv run python -m microstructure.live --symbols BTCUSDT,ETHUSDT
    uv run python -m microstructure.live --symbols BTCUSDT --window 30 --record
"""
