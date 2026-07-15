"""Backtest engine.

Phase 3 provides a **deterministic, vector-free, PIT-safe** engine so we can
prove the semantics before wiring in vectorbt. Frictions and next-open fills
are explicit; the risk engine gates orders separately.
"""
from packages.backtest.engine import (
    BacktestConfig, BacktestResult, run_daily_signal_backtest, Trade,
)

__all__ = [
    "BacktestConfig", "BacktestResult", "run_daily_signal_backtest", "Trade",
]
