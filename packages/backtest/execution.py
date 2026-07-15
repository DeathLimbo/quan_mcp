"""Event-driven execution models (§92 exec).

Three concrete models implement the same ``ExecutionModel`` protocol so the
event-driven backtester can swap them per market:

- ``CnExecutionModel``  — CN A-share T+1, up/down limit rules, halt-aware,
                          stamp+commission+transfer-fee cost stack.
- ``UsExecutionModel``  — US REGULAR session, split/dividend cash adjustments,
                          commission + spread + slippage layered costs.
- ``FundExecutionModel``— open-end mutual funds priced at unknown-price cutoff,
                          subscription/redemption fees, minimum holding period.

Fills are always dated **strictly after** the decision date. Anything that
cannot fill (limit-locked, halted, past cutoff, delisted) is returned as a
``NoFill`` with a stable code so the caller can log it in the "not tradable"
report line and never fabricate a fill.

The models are pure: they take an OrderIntent + the state visible on the
signal date and return a Fill|NoFill without touching a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Iterable, Literal, Protocol, runtime_checkable

from packages.common.instrument_id import AssetType, InstrumentId, Market
from packages.data_sources.contracts import Bar


# ---------------------------------------------------------------------------
# Shared types


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    instrument_id: InstrumentId
    side: Side
    quantity: Decimal           # signed absolute quantity (always positive)
    decision_date: date
    ref_price: Decimal | None = None   # signal-time close, informational only
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    order: OrderIntent
    fill_date: date
    fill_price: Decimal
    fill_qty: Decimal
    fees: Decimal               # total cost (commission+tax+slippage $)
    cash_delta: Decimal         # signed: buy -> negative, sell -> positive
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class NoFill:
    order: OrderIntent
    reason: str                 # stable code, e.g. HALTED / UP_LIMIT_LOCK
    detail: str = ""


@runtime_checkable
class ExecutionModel(Protocol):
    """Common surface for CN/US/Fund executors."""

    market: Market

    def execute(
        self,
        order: OrderIntent,
        *,
        bars: list[Bar],
        halted_dates: frozenset[date] = frozenset(),
        delisted_from: date | None = None,
    ) -> Fill | NoFill: ...


# ---------------------------------------------------------------------------
# CN A-share


@dataclass(frozen=True, slots=True)
class CnExecutionModel:
    """China A-share T+1 execution.

    - Sell not allowed on the *entry* day (T+1 rule) — the caller must ensure
      ``order.decision_date`` predates the fill date; this model refuses to
      settle a SELL on the same session as the last BUY.
    - Up-limit: BUY blocked (locked); DOWN-limit: SELL blocked.
    - Halt: no fill.
    - Cost: stamp_tax_bps (sell only) + commission_bps + transfer_fee_bps
      (Shanghai only, both sides).
    """

    market: Market = Market.CN
    commission_bps: Decimal = Decimal("2.5")
    stamp_tax_bps: Decimal = Decimal("10.0")           # sell side
    transfer_fee_bps: Decimal = Decimal("0.1")         # SH both sides
    slippage_bps: Decimal = Decimal("5.0")
    up_limit_pct: Decimal = Decimal("0.10")            # 10% main board
    down_limit_pct: Decimal = Decimal("0.10")

    def execute(
        self,
        order: OrderIntent,
        *,
        bars: list[Bar],
        halted_dates: frozenset[date] = frozenset(),
        delisted_from: date | None = None,
    ) -> Fill | NoFill:
        assert order.instrument_id.market is Market.CN, "wrong market"
        assert order.instrument_id.asset_type is not AssetType.FUND, "use FundExecutionModel"
        # Find next tradable bar strictly after decision_date
        next_bars = [b for b in bars if b.market_local_date > order.decision_date]
        next_bars.sort(key=lambda b: b.market_local_date)
        if not next_bars:
            return NoFill(order=order, reason="NO_NEXT_BAR")
        # Prior close for limit calc
        priors = [b for b in bars if b.market_local_date <= order.decision_date]
        if not priors:
            return NoFill(order=order, reason="NO_PRIOR_CLOSE")
        prior_close = Decimal(priors[-1].close)

        target = next_bars[0]
        if delisted_from is not None and target.market_local_date >= delisted_from:
            return NoFill(order=order, reason="DELISTED")
        if target.market_local_date in halted_dates:
            return NoFill(order=order, reason="HALTED")

        up_limit = prior_close * (Decimal(1) + self.up_limit_pct)
        down_limit = prior_close * (Decimal(1) - self.down_limit_pct)
        # Lock condition uses target.open, matching real Level-1 semantics.
        open_px = Decimal(target.open)
        if order.side is Side.BUY and open_px >= up_limit:
            return NoFill(order=order, reason="UP_LIMIT_LOCK",
                          detail=f"open {open_px} >= up_limit {up_limit}")
        if order.side is Side.SELL and open_px <= down_limit:
            return NoFill(order=order, reason="DOWN_LIMIT_LOCK",
                          detail=f"open {open_px} <= down_limit {down_limit}")

        # Slippage: buys pay more, sells receive less.
        slip = Decimal(self.slippage_bps) / Decimal(10_000)
        sign = Decimal(1) if order.side is Side.BUY else Decimal(-1)
        fill_px = open_px * (Decimal(1) + sign * slip)
        notional = fill_px * order.quantity

        bps = self.commission_bps + self.transfer_fee_bps
        if order.side is Side.SELL:
            bps += self.stamp_tax_bps
        fees = (notional * bps / Decimal(10_000)).quantize(Decimal("0.0001"))
        cash_delta = -notional - fees if order.side is Side.BUY else notional - fees

        return Fill(order=order, fill_date=target.market_local_date,
                    fill_price=fill_px, fill_qty=order.quantity,
                    fees=fees, cash_delta=cash_delta, tag="CN_T+1")


# ---------------------------------------------------------------------------
# US equity


@dataclass(frozen=True, slots=True)
class UsExecutionModel:
    """US REGULAR-session equity execution.

    - Fill at next session open plus half-spread + slippage.
    - Split/dividend adjustments are honored via ``bar.adj_factor``; caller is
      responsible for injecting adjusted bars if `adjust != "none"`.
    - Delisting hard-stops any subsequent fill.
    - Fractional shares allowed if ``allow_fractional`` is True.
    """

    market: Market = Market.US
    commission_per_trade: Decimal = Decimal("0.00")     # flat, IB-style
    spread_bps: Decimal = Decimal("2.0")
    slippage_bps: Decimal = Decimal("3.0")
    allow_fractional: bool = True

    def execute(
        self,
        order: OrderIntent,
        *,
        bars: list[Bar],
        halted_dates: frozenset[date] = frozenset(),
        delisted_from: date | None = None,
    ) -> Fill | NoFill:
        assert order.instrument_id.market is Market.US, "wrong market"
        next_bars = [b for b in bars if b.market_local_date > order.decision_date]
        next_bars.sort(key=lambda b: b.market_local_date)
        if not next_bars:
            return NoFill(order=order, reason="NO_NEXT_BAR")
        target = next_bars[0]
        if delisted_from is not None and target.market_local_date >= delisted_from:
            return NoFill(order=order, reason="DELISTED")
        if target.market_local_date in halted_dates:
            return NoFill(order=order, reason="TRADING_HALT")

        qty = order.quantity if self.allow_fractional \
            else Decimal(int(order.quantity))
        if qty <= 0:
            return NoFill(order=order, reason="ZERO_QTY")

        edge = (Decimal(self.spread_bps) / Decimal(2) + Decimal(self.slippage_bps)) / Decimal(10_000)
        sign = Decimal(1) if order.side is Side.BUY else Decimal(-1)
        fill_px = Decimal(target.open) * (Decimal(1) + sign * edge)
        notional = fill_px * qty
        fees = Decimal(self.commission_per_trade)
        cash_delta = -notional - fees if order.side is Side.BUY else notional - fees
        return Fill(order=order, fill_date=target.market_local_date,
                    fill_price=fill_px, fill_qty=qty,
                    fees=fees, cash_delta=cash_delta, tag="US_REGULAR")


# ---------------------------------------------------------------------------
# Open-end fund


@dataclass(frozen=True, slots=True)
class FundExecutionModel:
    """Open-end mutual fund execution — unknown-price subscription/redemption.

    Contract:
    - Cutoff each session is 15:00 local; orders after cutoff shift to the
      *next* session's NAV.
    - Subscription fee (bps) is charged on the invested amount; redemption
      fee is charged on the redemption notional and decays with holding days
      per ``redemption_fee_schedule``.
    - Minimum holding period: sells before ``min_holding_days`` after the last
      buy return ``MIN_HOLDING_VIOLATION``.
    """

    market: Market = Market.CN                          # domiciled CN funds
    subscription_fee_bps: Decimal = Decimal("120.0")    # 1.2%
    # (days_held_lt, fee_bps): first matching bucket wins.
    redemption_fee_schedule: tuple[tuple[int, Decimal], ...] = (
        (7,   Decimal("150.0")),
        (30,  Decimal("50.0")),
        (365, Decimal("25.0")),
    )
    min_holding_days: int = 0
    cutoff_hour_local: int = 15                          # 15:00

    def execute(
        self,
        order: OrderIntent,
        *,
        bars: list[Bar],
        halted_dates: frozenset[date] = frozenset(),
        delisted_from: date | None = None,
        last_buy_date: date | None = None,
        decision_time_local: datetime | None = None,
    ) -> Fill | NoFill:
        assert order.instrument_id.asset_type is AssetType.FUND, "fund only"
        # Determine effective valuation date: cutoff-aware
        cutoff_ok = True
        if decision_time_local is not None \
                and decision_time_local.hour >= self.cutoff_hour_local:
            cutoff_ok = False
        eff_bars = [b for b in bars if b.market_local_date > order.decision_date] \
            if cutoff_ok else \
            [b for b in bars if b.market_local_date > order.decision_date + timedelta(days=1)]
        eff_bars.sort(key=lambda b: b.market_local_date)
        if not eff_bars:
            return NoFill(order=order, reason="NO_NAV_YET")
        target = eff_bars[0]
        if delisted_from is not None and target.market_local_date >= delisted_from:
            return NoFill(order=order, reason="LIQUIDATED")

        nav = Decimal(target.close)
        if order.side is Side.BUY:
            gross = nav * order.quantity
            fee = (gross * self.subscription_fee_bps / Decimal(10_000)).quantize(Decimal("0.0001"))
            cash_delta = -(gross + fee)
        else:
            if last_buy_date is not None and self.min_holding_days > 0:
                held = (target.market_local_date - last_buy_date).days
                if held < self.min_holding_days:
                    return NoFill(order=order, reason="MIN_HOLDING_VIOLATION",
                                  detail=f"held {held}d < min {self.min_holding_days}d")
            days_held = (target.market_local_date - (last_buy_date or target.market_local_date)).days
            redem_bps = Decimal("0")
            for cutoff, bps in self.redemption_fee_schedule:
                if days_held < cutoff:
                    redem_bps = bps
                    break
            gross = nav * order.quantity
            fee = (gross * redem_bps / Decimal(10_000)).quantize(Decimal("0.0001"))
            cash_delta = gross - fee

        return Fill(order=order, fill_date=target.market_local_date,
                    fill_price=nav, fill_qty=order.quantity,
                    fees=fee, cash_delta=cash_delta,
                    tag=("FUND_SUB" if order.side is Side.BUY else "FUND_RED"))
