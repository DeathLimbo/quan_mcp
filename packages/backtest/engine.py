"""Single-instrument daily backtest.

Rules:
- Signals are computed at t (close-of-t data, PIT).
- Orders are executed at NEXT session's open with configurable slippage.
- Costs = commission_bps + slippage_bps on both legs.
- Position is +1/0/-1 (long-only in v1 unless short_allowed).
- ``ledger_paper`` uses Decimal-like float here; production paper trader uses
  Decimal to match spec §复式记账账本.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Sequence

from packages.data_sources.contracts import Bar


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    short_allowed: bool = False
    initial_cash: float = 1_000_000.0
    max_pos: float = 1.0            # fraction of equity


@dataclass(frozen=True, slots=True)
class Trade:
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    side: int            # +1 long, -1 short
    ret_gross: float
    ret_net: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    total_return: float = 0.0
    n_trades: int = 0


SignalFn = Callable[[list[Bar]], int]   # returns +1 / 0 / -1


def run_daily_signal_backtest(
    bars: Sequence[Bar],
    signal_fn: SignalFn,
    *,
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    cfg = cfg or BacktestConfig()
    bars_sorted = sorted(bars, key=lambda b: b.market_local_date)
    if len(bars_sorted) < 2:
        return BacktestResult()

    result = BacktestResult()
    equity = cfg.initial_cash
    position = 0
    entry_date: date | None = None
    entry_price = 0.0

    for i in range(len(bars_sorted) - 1):
        window = bars_sorted[: i + 1]
        signal = signal_fn(window)
        next_bar = bars_sorted[i + 1]
        # Fill at next open with slippage
        fill_px = float(next_bar.open) * (1.0 + cfg.slippage_bps / 1e4 * (1 if signal > position else -1 if signal < position else 0))
        # Change position if signal differs
        if signal != position:
            if position != 0 and entry_date is not None:
                # Close previous
                gross = (fill_px / entry_price - 1.0) * position
                cost = 2.0 * (cfg.commission_bps + cfg.slippage_bps) / 1e4
                net = gross - cost
                result.trades.append(Trade(
                    entry_date=entry_date, exit_date=next_bar.market_local_date,
                    entry_price=entry_price, exit_price=fill_px,
                    side=position, ret_gross=gross, ret_net=net,
                ))
                equity *= (1.0 + net * cfg.max_pos)
            position = signal if (cfg.short_allowed or signal >= 0) else 0
            entry_date = next_bar.market_local_date if position != 0 else None
            entry_price = fill_px if position != 0 else 0.0

        # Mark-to-market equity for the curve
        mtm = float(next_bar.close)
        pnl_frac = (mtm / entry_price - 1.0) * position * cfg.max_pos if position and entry_price > 0 else 0.0
        result.equity_curve.append((next_bar.market_local_date, equity * (1.0 + pnl_frac)))

    result.n_trades = len(result.trades)
    if result.equity_curve:
        result.total_return = result.equity_curve[-1][1] / cfg.initial_cash - 1.0
    return result
