"""Microstructure research area.

Live market-data capture from Bybit (via ccxt.pro) and tooling to run
short-horizon (seconds-to-minutes, scalping-style) microstructure
experiments on the captured streams.

Sub-packages:
    collector  -- the live WebSocket capture CLI/TUI
Modules:
    loader     -- read captured sessions back into pandas DataFrames
"""
