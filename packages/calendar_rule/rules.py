"""Market microstructure rules: price limits, tick size, lot size.

CN A-share:
- Main board: +/- 10%
- ST stocks:  +/- 5%
- STAR Market (688xxx) & ChiNext (300/301): +/- 20%
- Beijing Exchange: +/- 30%

US:
- No daily price limit (halt bands via LULD apply intraday, not modeled here).
- Penny stocks (< $5) have specific rules; tick size varies.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue


RULE_VERSION = "rule-v0.1"


def rule_version() -> str:
    return RULE_VERSION


@dataclass(frozen=True)
class PriceLimitRule:
    market: Market
    up_pct: Decimal | None      # None = no daily limit
    down_pct: Decimal | None
    reason: str


def _is_star(symbol: str) -> bool:
    return symbol.startswith(("688", "689"))


def _is_chinext(symbol: str) -> bool:
    return symbol.startswith(("300", "301"))


def _is_st(name_local: str | None) -> bool:
    if not name_local:
        return False
    return name_local.startswith(("ST", "*ST", "ST ", "*ST "))


def get_price_limit(iid: InstrumentId, name_local: str | None = None) -> PriceLimitRule:
    if iid.market is Market.US:
        return PriceLimitRule(Market.US, None, None, "US: LULD only, no daily band")

    # CN branch
    if iid.venue is Venue.BSE:
        return PriceLimitRule(Market.CN, Decimal("0.30"), Decimal("-0.30"), "BSE main")

    if iid.asset_type is AssetType.ETF:
        return PriceLimitRule(Market.CN, Decimal("0.10"), Decimal("-0.10"), "CN ETF default")

    if iid.asset_type is AssetType.EQUITY:
        if _is_st(name_local):
            return PriceLimitRule(Market.CN, Decimal("0.05"), Decimal("-0.05"), "ST equity")
        if _is_star(iid.symbol) or _is_chinext(iid.symbol):
            return PriceLimitRule(Market.CN, Decimal("0.20"), Decimal("-0.20"), "STAR/ChiNext")
        return PriceLimitRule(Market.CN, Decimal("0.10"), Decimal("-0.10"), "CN main")

    # FUND, INDEX etc: no explicit daily limit at this level
    return PriceLimitRule(Market.CN, None, None, f"no rule for {iid.asset_type}")
