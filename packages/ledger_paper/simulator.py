"""Simulated broker + reconciliation — spec §15 执行模拟 + 里程碑6.

The ledger (:mod:`packages.ledger_paper.ledger`) provides the accounting
(``OrderIntent`` / ``PaperFill`` / ``Portfolio.apply_fill``). This module adds
the *execution* layer: a :class:`SimulatedBroker` that turns an
``OrderIntent`` plus a session bar into a ``PaperFill`` (or rejects it),
applying slippage and commission so paper-trading stays realistic.

Reconciliation (:func:`reconcile_forecast_vs_fills`) compares the model's
forecasted scores against realised fills — the gap exposes slippage, rejected
limits and missed signals (spec §15.4 共同偏差, §27 复盘).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from packages.data_sources.contracts import Bar
from packages.ledger_paper.ledger import OrderIntent, PaperFill


@dataclass
class SimulatedBroker:
    """Turn :class:`OrderIntent` + :class:`Bar` into :class:`PaperFill`.

    - Market orders (``limit_price is None``): fill at ``close`` adjusted by
      ``slippage_bps`` in the adverse direction.
    - Limit orders: fill only if the limit is reachable within the bar's
      ``[low, high]`` range; fill price is the limit (no adverse slippage on
      a resting limit that gets hit).
    - Commission: ``commission_bps`` of notional (``price * quantity``).
    """

    slippage_bps: Decimal = Decimal("5")      # 0.05%
    commission_bps: Decimal = Decimal("3")    # 0.03%
    min_notional: Decimal = Decimal("0")      # reject dust orders

    def fill_order(self, intent: OrderIntent, bar: Bar,
                   *, now: datetime | None = None) -> PaperFill | None:
        """Return a PaperFill, or None if the order cannot be filled this bar."""
        now = now or datetime.now(timezone.utc)
        qty = intent.quantity
        if qty <= 0:
            return None

        if intent.limit_price is None:
            # market order: close +/- slippage (adverse)
            slip = Decimal(1) + (self.slippage_bps / Decimal(10000)) * intent.side
            fill_price = (bar.close * slip).quantize(Decimal("0.0001"))
        else:
            lp = intent.limit_price
            # buy (+1): fill if low <= lp; sell (-1): fill if high >= lp
            if intent.side > 0 and bar.low > lp:
                return None
            if intent.side < 0 and bar.high < lp:
                return None
            fill_price = lp

        notional = fill_price * qty
        if notional < self.min_notional:
            return None
        fee = (notional * self.commission_bps / Decimal(10000)).quantize(Decimal("0.01"))

        return PaperFill(
            order_intent_id=intent.intent_id,
            instrument_id=intent.instrument_id,
            side=intent.side,
            filled_quantity=qty,
            filled_price=fill_price,
            fee=fee,
            filled_at_utc=now,
        )

    def fill_batch(self, intents: Sequence[OrderIntent], bar_by_iid: dict,
                   *, now: datetime | None = None) -> list[PaperFill]:
        """Fill many intents; each must have its instrument's bar available."""
        fills: list[PaperFill] = []
        for intent in intents:
            bar = bar_by_iid.get(intent.instrument_id)
            if bar is None:
                continue
            f = self.fill_order(intent, bar, now=now)
            if f is not None:
                fills.append(f)
        return fills


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    instrument_id: str
    forecast_score: float | None
    side: int
    filled: bool
    fill_price: float | None
    benchmark_price: float       # bar close — the "ideal" price
    slippage_bps: float | None   # realised adverse slippage; None if not filled
    fee: float | None


def reconcile_forecast_vs_fills(
    intents: Sequence[OrderIntent],
    fills: Sequence[PaperFill],
    bars: dict,  # InstrumentId -> Bar
    scores: dict[str, float] | None = None,
) -> list[ReconciliationRow]:
    """Compare forecasted signals against realised fills (spec §15.4 / §27).

    Exposes per-intent: was it filled, at what price vs the bar close
    (benchmark), the adverse slippage in bps, and the fee. Unfilled limit
    orders show ``filled=False`` — a signal that the model wanted to trade but
    the market didn't reach the limit.
    """
    fill_by_intent = {f.order_intent_id: f for f in fills}
    rows: list[ReconciliationRow] = []
    for intent in intents:
        bar = bars.get(intent.instrument_id)
        bench = float(bar.close) if bar else 0.0
        f = fill_by_intent.get(intent.intent_id)
        if f is not None:
            slip = (float(f.filled_price) - bench) * intent.side / bench * 10000 if bench else None
            rows.append(ReconciliationRow(
                instrument_id=intent.instrument_id.canonical(),
                forecast_score=scores.get(intent.instrument_id.canonical()) if scores else None,
                side=intent.side, filled=True,
                fill_price=float(f.filled_price), benchmark_price=bench,
                slippage_bps=slip, fee=float(f.fee),
            ))
        else:
            rows.append(ReconciliationRow(
                instrument_id=intent.instrument_id.canonical(),
                forecast_score=scores.get(intent.instrument_id.canonical()) if scores else None,
                side=intent.side, filled=False,
                fill_price=None, benchmark_price=bench,
                slippage_bps=None, fee=None,
            ))
    return rows
