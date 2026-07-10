"""Live Bybit market-data collector.

Run headless:
    uv run python -m microstructure.collector --symbols BTCUSDT,ETHUSDT --no-ui

Run with the interactive Textual dashboard (default):
    uv run python -m microstructure.collector --symbols BTCUSDT,ETHUSDT
"""
from microstructure.collector.session import CaptureSession, SessionConfig

__all__ = ["CaptureSession", "SessionConfig"]
