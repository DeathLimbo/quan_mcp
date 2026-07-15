"""Deterministic fake market-data adapter for tests and offline dev.

Generates a reproducible price walk. No network. Time is UTC-first.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
from typing import Iterable, Iterator, Literal

from packages.calendar_rule import calendar_version, get_calendar, rule_version
from packages.common.instrument_id import AssetType, InstrumentId, Market
from packages.data_sources.contracts import (
    Bar, InstrumentDescriptor, MarketDataAdapter,
)


class FakeMarketDataAdapter(MarketDataAdapter):
    adapter_id = "fake"
    supports_markets = frozenset({Market.CN, Market.US})
    supports_asset_types = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.FUND})

    def __init__(self, universe: list[InstrumentDescriptor] | None = None) -> None:
        self._universe = universe or []

    def list_instruments(self, market: Market) -> Iterable[InstrumentDescriptor]:
        return [d for d in self._universe if d.instrument_id.market is market]

    def fetch_bars_daily(
        self,
        instrument_id: InstrumentId,
        start: date,
        end: date,
        *,
        adjust: Literal["none", "forward", "backward"] = "none",
    ) -> Iterator[Bar]:
        cal = get_calendar(instrument_id.market)
        seed = int(hashlib.sha256(instrument_id.canonical().encode()).hexdigest(), 16)
        # Simple deterministic walk anchored at 100.0
        base = Decimal("100.00")
        for d in cal.sessions_between(start, end):
            offset = ((seed + d.toordinal()) % 200 - 100) / Decimal("1000")  # +/- 0.1
            price = (base + Decimal(offset)).quantize(Decimal("0.01"))
            close_time_local = datetime.combine(d, time(15, 0)) if instrument_id.market is Market.CN \
                else datetime.combine(d, time(16, 0))
            # 简化：认为收盘时刻的 UTC 就是 local + 相应偏移；此处直接以 UTC 记录
            evt_utc = close_time_local.replace(tzinfo=timezone.utc)
            yield Bar(
                instrument_id=instrument_id,
                event_time_utc=evt_utc,
                market_local_date=d,
                open=price,
                high=(price + Decimal("0.50")).quantize(Decimal("0.01")),
                low=(price - Decimal("0.50")).quantize(Decimal("0.01")),
                close=price,
                volume=Decimal("1000000"),
                turnover=(price * Decimal("1000000")).quantize(Decimal("0.01")),
                adj_factor=Decimal("1.0"),
                available_at_utc=evt_utc + timedelta(hours=1),
                source=self.adapter_id,
                calendar_version=calendar_version(),
                rule_version=rule_version(),
                source_version="fake.v1",
                license_tag="INTERNAL_RESEARCH",
                quality_status="NORMAL",
            )
