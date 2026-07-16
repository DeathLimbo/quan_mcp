"""Adapter contracts (Protocol-based). No provider imports here."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Iterator, Literal, Protocol, runtime_checkable

from packages.common.errors import QuantError
from packages.common.instrument_id import AssetType, InstrumentId, Market


class ProviderError(QuantError):
    """Non-recoverable provider error (auth, 5xx after retry, malformed)."""


class RateLimitError(QuantError):
    """Provider throttled us. Caller should backoff + retry."""


@dataclass(frozen=True, slots=True)
class InstrumentDescriptor:
    instrument_id: InstrumentId
    name_local: str | None
    name_en: str | None
    currency: str
    lot_size: int | None
    first_trade_date: date | None
    last_trade_date: date | None
    status: Literal["ACTIVE", "SUSPENDED", "DELISTED"] = "ACTIVE"


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: InstrumentId
    event_time_utc: datetime          # session close in UTC
    market_local_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal | None
    adj_factor: Decimal | None        # cumulative adjustment factor
    available_at_utc: datetime        # when this row became consumable
    source: str                       # adapter id
    calendar_version: str
    rule_version: str
    # Provenance (spec §7.4 unified Adapter contract). Defaults keep the
    # dataclass backwards compatible with older constructions while newer
    # adapters stamp real values.
    source_version: str = "unspecified"
    license_tag: str = "INTERNAL_RESEARCH"
    quality_status: str = "NORMAL"


@dataclass(frozen=True, slots=True)
class FundNAV:
    instrument_id: InstrumentId
    market_local_date: date
    event_time_utc: datetime
    unit_nav: Decimal
    accum_nav: Decimal | None
    available_at_utc: datetime
    source: str
    # Provenance (spec §7.4).
    source_version: str = "unspecified"
    license_tag: str = "INTERNAL_RESEARCH"
    quality_status: str = "NORMAL"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    instrument_id: InstrumentId
    action_type: Literal["SPLIT", "DIVIDEND", "MERGER", "SPINOFF", "RIGHTS"]
    announcement_date_utc: datetime
    ex_date_local: date
    payable_date_local: date | None
    ratio: Decimal | None      # split ratio / dividend per share
    currency: str | None
    source: str
    available_at_utc: datetime
    # Provenance (spec §7.4).
    source_version: str = "unspecified"
    license_tag: str = "INTERNAL_RESEARCH"
    quality_status: str = "NORMAL"


@runtime_checkable
class MarketDataAdapter(Protocol):
    """Historical + latest OHLCV. Live tick feeds are out of scope for V1."""

    adapter_id: str
    supports_markets: frozenset[Market]
    supports_asset_types: frozenset[AssetType]

    def list_instruments(self, market: Market) -> Iterable[InstrumentDescriptor]: ...

    def fetch_bars_daily(
        self,
        instrument_id: InstrumentId,
        start: date,
        end: date,
        *,
        adjust: Literal["none", "forward", "backward"] = "none",
    ) -> Iterator[Bar]: ...


@runtime_checkable
class FundamentalAdapter(Protocol):
    adapter_id: str

    def fetch_fund_navs(
        self, instrument_id: InstrumentId, start: date, end: date
    ) -> Iterator[FundNAV]: ...


@dataclass(frozen=True, slots=True)
class FxRate:
    """Point-in-time FX rate: 1 unit of ``base_ccy`` = ``rate`` units of ``quote_ccy``.

    PIT contract: ``available_at_utc`` is when the rate became observable
    (EOD lag mirrors the equity adapters so downstream code cannot read a
    same-day close before it is published). Mirrors spec §3.2 FX Adapter +
    §2.1.6 (local vs base currency attribution).
    """
    base_ccy: str
    quote_ccy: str
    market_local_date: date
    rate: Decimal
    event_time_utc: datetime
    available_at_utc: datetime
    source: str
    source_version: str = "unspecified"
    license_tag: str = "INTERNAL_RESEARCH"
    quality_status: str = "NORMAL"


@runtime_checkable
class FxAdapter(Protocol):
    """Historical FX rates. V1 wires USD/CNY (and inverses) only."""

    adapter_id: str

    def fetch_fx_rates(
        self, *, base: str, quote: str, start: date, end: date,
    ) -> Iterator[FxRate]: ...


@runtime_checkable
class CorporateActionAdapter(Protocol):
    adapter_id: str

    def fetch_actions(
        self, instrument_id: InstrumentId, start: date, end: date
    ) -> Iterator[CorporateAction]: ...
