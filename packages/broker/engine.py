"""Broker adapter layer + paper-trading engine — task #32, spec §34 执行系统.

Decouples the trading decision loop from the execution venue:

- :class:`BrokerAdapter` (Protocol) — unified interface any broker must
  satisfy: ``place_order`` (intent+bar → fill or None), ``get_positions``,
  ``get_cash``. Real brokers (Futu/IB/Alpaca) implement this against their
  SDK; we ship :class:`SimulatedBrokerAdapter` wrapping the ledger's
  :class:`~packages.ledger_paper.simulator.SimulatedBroker`.

- :class:`PaperTradingEngine` — the full decision loop:
  forecast (PRODUCTION model) → signal (score thresholds) → OrderIntent →
  broker.place_order → PaperFill → Portfolio.apply_fill → reconciliation.
  This is the Gate-4 simulated-trading track (spec 里程碑6): runs on real
  bars, produces real fills + P&L, but no real money moves.

Signal logic: score > ``buy_threshold`` → long; score < ``sell_threshold``
→ close. Position sizing: equal-weight by available cash (spec §29 仓位管理).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Sequence

from packages.common.instrument_id import InstrumentId
from packages.data_sources.contracts import Bar
from packages.ledger_paper.ledger import (
    Currency, OrderIntent, PaperFill, Portfolio, Position,
)
from packages.ledger_paper.simulator import (
    SimulatedBroker, ReconciliationRow, reconcile_forecast_vs_fills,
)


class BrokerAdapter(Protocol):
    """Unified broker interface. Real brokers (Futu/IB/Alpaca) implement this."""

    def place_order(self, intent: OrderIntent, bar: Bar,
                    *, now: datetime | None = None) -> PaperFill | None: ...
    def get_positions(self) -> list[Position]: ...
    def get_cash(self, ccy: Currency | str) -> Decimal: ...


@dataclass
class SimulatedBrokerAdapter:
    """In-memory broker backed by :class:`SimulatedBroker` + :class:`Portfolio`."""

    broker: SimulatedBroker
    portfolio: Portfolio

    def place_order(self, intent: OrderIntent, bar: Bar,
                    *, now: datetime | None = None) -> PaperFill | None:
        fill = self.broker.fill_order(intent, bar, now=now)
        if fill is not None:
            ccy = Currency.USD if intent.instrument_id.market.value == "US" else Currency.CNY
            self.portfolio.apply_fill(fill, ccy=ccy)
        return fill

    def get_positions(self) -> list[Position]:
        return self.portfolio.positions()

    def get_cash(self, ccy: Currency | str = Currency.USD) -> Decimal:
        if isinstance(ccy, str):
            ccy = Currency(ccy)
        return self.portfolio.cash(ccy)


@dataclass
class TradingSignal:
    instrument_id: InstrumentId
    side: int               # +1 buy, -1 sell, 0 hold
    score: float
    bar: Bar


@dataclass
class CycleResult:
    as_of: datetime
    signals: list[TradingSignal] = field(default_factory=list)
    intents: list[OrderIntent] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)
    reconciliation: list[ReconciliationRow] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    cash: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class PaperTradingEngine:
    """Full decision loop: forecast → signal → order → fill → portfolio.

    ``forecast_fn(iid, as_of, bars) -> score | None`` is injected so the
    engine stays decoupled from the inference service (callers wire the
    PRODUCTION model's score method).
    """

    broker: BrokerAdapter
    portfolio: Portfolio
    buy_threshold: float = 0.60
    sell_threshold: float = 0.40
    max_weight: float = 0.25      # §29 max single-name weight

    def run_cycle(
        self,
        bars_by_iid: dict[InstrumentId, Sequence[Bar]],
        as_of: datetime,
        forecast_fn,
        *,
        base_currency: Currency = Currency.USD,
    ) -> CycleResult:
        """One trading cycle: score universe, generate signals, fill, reconcile."""
        import uuid

        result = CycleResult(as_of=as_of)
        # latest bar per instrument (the bar available at as_of)
        latest_by_iid: dict[InstrumentId, Bar] = {}
        for iid, bars in bars_by_iid.items():
            visible = [b for b in bars if b.available_at_utc <= as_of]
            if visible:
                latest_by_iid[iid] = visible[-1]

        # 1) forecast + signal
        scores: dict[str, float] = {}
        for iid, bar in latest_by_iid.items():
            score = forecast_fn(iid, as_of, bars_by_iid[iid])
            if score is None:
                continue
            scores[iid.canonical()] = score
            side = 0
            if score >= self.buy_threshold:
                side = 1
            elif score <= self.sell_threshold:
                side = -1
            if side != 0:
                result.signals.append(TradingSignal(
                    instrument_id=iid, side=side, score=score, bar=bar))

        # 2) position sizing + order generation
        n_buy = sum(1 for s in result.signals if s.side > 0) or 1
        cash = self.broker.get_cash(base_currency)
        for sig in result.signals:
            if sig.side > 0:
                # equal-weight buy: cash/n_buys, capped by max_weight
                budget = cash * Decimal(str(self.max_weight))
                qty = (budget / sig.bar.close).quantize(Decimal("1")) if sig.bar.close > 0 else Decimal("0")
                if qty <= 0:
                    continue
            else:
                # sell: liquidate existing position
                pos = next((p for p in self.portfolio.positions()
                            if p.instrument_id == sig.instrument_id), None)
                qty = pos.quantity if pos else Decimal("0")
                if qty <= 0:
                    continue
            intent = OrderIntent(
                instrument_id=sig.instrument_id,
                side=sig.side, quantity=qty, limit_price=None,
                reason=f"model score={sig.score:.3f}",
                intent_id=f"ord_{uuid.uuid4().hex[:10]}",
            )
            result.intents.append(intent)

        # 3) fill
        for intent in result.intents:
            bar = latest_by_iid.get(intent.instrument_id)
            if bar is None:
                continue
            fill = self.broker.place_order(intent, bar, now=as_of)
            if fill is not None:
                result.fills.append(fill)

        # 4) reconcile
        result.reconciliation = reconcile_forecast_vs_fills(
            result.intents, result.fills, latest_by_iid, scores=scores)
        result.positions = self.broker.get_positions()
        for c in Currency:
            result.positions  # already fetched
        result.cash = {c.value: self.broker.get_cash(c) for c in Currency
                       if self.broker.get_cash(c) != Decimal("0")}
        return result
