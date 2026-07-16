"""Broker adapter layer + paper-trading engine — spec §34 执行系统."""
from packages.broker.engine import (
    BrokerAdapter, SimulatedBrokerAdapter, PaperTradingEngine,
    TradingSignal, CycleResult,
)

__all__ = [
    "BrokerAdapter", "SimulatedBrokerAdapter", "PaperTradingEngine",
    "TradingSignal", "CycleResult",
]
