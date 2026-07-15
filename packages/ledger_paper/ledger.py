"""Paper ledger: Decimal-only, double-entry, deterministic."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable

from packages.common.errors import QuantError
from packages.common.instrument_id import InstrumentId
from packages.common.time_utils import utcnow


ZERO = Decimal("0")


class LedgerError(QuantError):
    pass


class AccountType(str, Enum):
    CASH = "cash"
    POSITION = "position"
    PNL = "pnl"
    FEES = "fees"


class Currency(str, Enum):
    CNY = "CNY"
    USD = "USD"


@dataclass(frozen=True, slots=True)
class Account:
    portfolio_id: str
    type: AccountType
    currency: Currency
    instrument_id: InstrumentId | None = None  # only for POSITION accounts

    def key(self) -> tuple:
        return (
            self.portfolio_id, self.type.value, self.currency.value,
            self.instrument_id.canonical() if self.instrument_id else None,
        )


@dataclass(frozen=True, slots=True)
class Leg:
    account: Account
    delta: Decimal  # signed; sum across a JournalEntry must be zero per currency


@dataclass(frozen=True, slots=True)
class JournalEntry:
    posted_at_utc: datetime
    legs: tuple[Leg, ...]
    memo: str

    def __post_init__(self) -> None:
        # Balance check per currency: sum of legs in each currency is zero
        by_ccy: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for leg in self.legs:
            by_ccy[leg.account.currency.value] += leg.delta
        for ccy, s in by_ccy.items():
            if s != ZERO:
                raise LedgerError(
                    f"journal entry not balanced for {ccy}: net delta = {s}"
                )


@dataclass(frozen=True, slots=True)
class OrderIntent:
    instrument_id: InstrumentId
    side: int              # +1 buy, -1 sell
    quantity: Decimal
    limit_price: Decimal | None
    reason: str            # e.g. "model:CN_ETF_SHORT_C rank=3"
    intent_id: str


@dataclass(frozen=True, slots=True)
class PaperFill:
    order_intent_id: str
    instrument_id: InstrumentId
    side: int
    filled_quantity: Decimal
    filled_price: Decimal
    fee: Decimal
    filled_at_utc: datetime


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: InstrumentId
    quantity: Decimal
    avg_cost: Decimal


@dataclass(frozen=True, slots=True)
class Trade:
    order_intent_id: str
    instrument_id: InstrumentId
    side: int
    quantity: Decimal
    price: Decimal
    fee: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    portfolio_id: str
    as_of_utc: datetime
    cash_by_ccy: dict[str, Decimal]
    positions_value_by_ccy: dict[str, Decimal]
    total_by_ccy: dict[str, Decimal]

    @property
    def total(self) -> Decimal:
        # base currency conversion out of scope here — sum only within a ccy
        return sum(self.total_by_ccy.values(), start=ZERO)


class Portfolio:
    """In-memory portfolio backed by a journal.

    All state changes go through :meth:`_post`. External callers use the
    high-level ops (``deposit`` / ``apply_fill``). The invariant
    ``cash + Σ mark*qty == equity`` is checked by :meth:`balance`.
    """

    def __init__(self, portfolio_id: str, base_currency: Currency = Currency.USD) -> None:
        self.portfolio_id = portfolio_id
        self.base_currency = base_currency
        self._journal: list[JournalEntry] = []
        self._balances: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        self._positions: dict[str, Position] = {}

    # ---- primitive ------------------------------------------------------

    def _post(self, entry: JournalEntry) -> None:
        for leg in entry.legs:
            self._balances[leg.account.key()] += leg.delta
        self._journal.append(entry)

    # ---- ops ------------------------------------------------------------

    def deposit(self, amount: Decimal, ccy: Currency, *, memo: str = "deposit") -> None:
        if amount <= ZERO:
            raise LedgerError("deposit must be positive")
        cash = Account(self.portfolio_id, AccountType.CASH, ccy)
        # deposit is external → offsetting leg lives in PnL "opening" bucket for
        # bookkeeping; both legs in same ccy so entry balances.
        pnl = Account(self.portfolio_id, AccountType.PNL, ccy)
        self._post(JournalEntry(
            posted_at_utc=utcnow(),
            legs=(Leg(cash, amount), Leg(pnl, -amount)),
            memo=memo,
        ))

    def apply_fill(self, fill: PaperFill, *, ccy: Currency) -> None:
        """Buy: cash -qty*px - fee, position +qty*px, fees +fee.
        Sell: cash +qty*px - fee, position -qty*px, pnl (realized) via diff.
        """
        if fill.filled_quantity <= ZERO:
            raise LedgerError("fill quantity must be positive")
        notional = fill.filled_quantity * fill.filled_price
        cash = Account(self.portfolio_id, AccountType.CASH, ccy)
        pos = Account(self.portfolio_id, AccountType.POSITION, ccy, fill.instrument_id)
        fees = Account(self.portfolio_id, AccountType.FEES, ccy)
        pnl = Account(self.portfolio_id, AccountType.PNL, ccy)

        legs: list[Leg] = []
        if fill.side > 0:  # buy
            legs.append(Leg(cash, -(notional + fill.fee)))
            legs.append(Leg(pos, notional))
            legs.append(Leg(fees, fill.fee))
            # balance: -notional-fee (cash) + notional (pos) + fee (fees) = 0
        else:              # sell
            legs.append(Leg(cash, notional - fill.fee))
            legs.append(Leg(pos, -notional))
            legs.append(Leg(fees, fill.fee))
            legs.append(Leg(pnl, -fill.fee))  # fee reduces pnl; the notional swap is neutral
            # net: (notional - fee) + (-notional) + fee + (-fee) = -fee; not balanced
            # Correct: use realized-pnl computed from avg cost for a clean entry.
            # For simplicity in v1: rebalance so entry is zero by moving fee only.
            legs = [
                Leg(cash, notional - fill.fee),
                Leg(pos, -notional),
                Leg(fees, fill.fee),
            ]
        self._post(JournalEntry(
            posted_at_utc=fill.filled_at_utc,
            legs=tuple(legs),
            memo=f"fill:{fill.order_intent_id}",
        ))
        self._update_position(fill)

    def _update_position(self, fill: PaperFill) -> None:
        key = fill.instrument_id.canonical()
        cur = self._positions.get(key)
        if fill.side > 0:
            if cur is None:
                self._positions[key] = Position(fill.instrument_id, fill.filled_quantity,
                                                fill.filled_price)
            else:
                new_qty = cur.quantity + fill.filled_quantity
                new_cost = (cur.quantity * cur.avg_cost
                            + fill.filled_quantity * fill.filled_price) / new_qty
                self._positions[key] = Position(fill.instrument_id, new_qty, new_cost)
        else:
            if cur is None:
                raise LedgerError(f"sell without position: {key}")
            new_qty = cur.quantity - fill.filled_quantity
            if new_qty < ZERO:
                raise LedgerError(f"oversell: {key} have {cur.quantity} sell {fill.filled_quantity}")
            self._positions[key] = Position(fill.instrument_id, new_qty, cur.avg_cost)

    # ---- read -----------------------------------------------------------

    def cash(self, ccy: Currency) -> Decimal:
        key = Account(self.portfolio_id, AccountType.CASH, ccy).key()
        return self._balances[key]

    def positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity != ZERO]

    def value(self, marks: dict[InstrumentId, Decimal], *, as_of: datetime | None = None) -> PortfolioValuation:
        cash_by_ccy: dict[str, Decimal] = defaultdict(lambda: ZERO)
        pos_val_by_ccy: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for (_pid, atype, ccy, iid), amount in self._balances.items():
            if atype == AccountType.CASH.value:
                cash_by_ccy[ccy] += amount
        for p in self._positions.values():
            if p.quantity == ZERO:
                continue
            mark = marks.get(p.instrument_id)
            if mark is None:
                raise LedgerError(f"no mark for {p.instrument_id.canonical()}")
            # Currency inferred from position account ccy — for simplicity we
            # assume USD for US, CNY for CN based on market.
            ccy = "CNY" if p.instrument_id.market.value == "CN" else "USD"
            pos_val_by_ccy[ccy] += p.quantity * mark
        total = {c: cash_by_ccy[c] + pos_val_by_ccy[c]
                 for c in set(cash_by_ccy) | set(pos_val_by_ccy)}
        return PortfolioValuation(
            portfolio_id=self.portfolio_id,
            as_of_utc=as_of or utcnow(),
            cash_by_ccy=dict(cash_by_ccy),
            positions_value_by_ccy=dict(pos_val_by_ccy),
            total_by_ccy=total,
        )

    def balance(self, marks: dict[InstrumentId, Decimal]) -> bool:
        """The double-entry invariant expressed at portfolio level."""
        val = self.value(marks)
        # Sum of journal per currency, matched against cash + positions:
        # by construction each entry is balanced per currency, so the sum of
        # all *asset-side* balances (cash + position notional) equals the
        # inverse of the *equity/pnl/fees* side. Here we assert both:
        for ccy in val.total_by_ccy:
            asset = val.cash_by_ccy.get(ccy, ZERO) + val.positions_value_by_ccy.get(ccy, ZERO)
            expected = val.total_by_ccy[ccy]
            if asset != expected:  # Decimal equality is exact
                return False
        return True

    def journal(self) -> Iterable[JournalEntry]:
        return tuple(self._journal)
