"""Canonical instrument identity.

InstrumentId is the *only* uniqueness key in this system. `ticker` alone is
never enough:
- CN A-shares: 600519.SH and 600519.SZ are distinct venues
- ADR vs local: BABA.US (NYSE) != 09988.HK
- Delisted / relisted tickers may collide

Canonical form: ``{market}.{venue}.{asset_type}.{symbol}``
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    CN = "CN"
    US = "US"


class Venue(str, Enum):
    # CN
    SSE = "SSE"       # Shanghai
    SZSE = "SZSE"     # Shenzhen
    BSE = "BSE"       # Beijing
    CFFEX = "CFFEX"
    # US
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    ARCA = "ARCA"
    BATS = "BATS"
    OTC = "OTC"
    # Fund domiciles
    CN_FUND = "CN_FUND"
    US_FUND = "US_FUND"


class AssetType(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUND = "FUND"
    INDEX = "INDEX"
    FX = "FX"


_ALLOWED = {
    Market.CN: {
        AssetType.EQUITY: {Venue.SSE, Venue.SZSE, Venue.BSE},
        AssetType.ETF:    {Venue.SSE, Venue.SZSE},
        AssetType.FUND:   {Venue.CN_FUND},
        AssetType.INDEX:  {Venue.SSE, Venue.SZSE, Venue.CFFEX},
    },
    Market.US: {
        AssetType.EQUITY: {Venue.NASDAQ, Venue.NYSE, Venue.ARCA, Venue.BATS, Venue.OTC},
        AssetType.ETF:    {Venue.NASDAQ, Venue.NYSE, Venue.ARCA, Venue.BATS},
        AssetType.FUND:   {Venue.US_FUND},
        AssetType.INDEX:  {Venue.NYSE, Venue.NASDAQ},
    },
}


@dataclass(frozen=True, slots=True)
class InstrumentId:
    market: Market
    venue: Venue
    asset_type: AssetType
    symbol: str

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        allowed = _ALLOWED.get(self.market, {}).get(self.asset_type, set())
        if self.venue not in allowed:
            raise ValueError(
                f"venue {self.venue} not allowed for {self.market}/{self.asset_type}"
            )
        # symbol is stored uppercase, no whitespace
        object.__setattr__(self, "symbol", self.symbol.strip().upper())

    def canonical(self) -> str:
        return f"{self.market.value}.{self.venue.value}.{self.asset_type.value}.{self.symbol}"

    def __str__(self) -> str:  # pragma: no cover
        return self.canonical()


def parse_instrument_id(s: str) -> InstrumentId:
    """Parse ``CN.SSE.EQUITY.600519`` -> InstrumentId."""
    parts = s.split(".")
    if len(parts) != 4:
        raise ValueError(f"invalid InstrumentId canonical form: {s!r}")
    market, venue, asset_type, symbol = parts
    return InstrumentId(
        market=Market(market),
        venue=Venue(venue),
        asset_type=AssetType(asset_type),
        symbol=symbol,
    )
