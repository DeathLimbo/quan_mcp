"""UTC-first time utilities and market clocks.

Rules:
- All timestamps stored / passed across boundaries MUST be timezone-aware UTC.
- ``market_local_time`` is derived only for display / calendar decisions.
- DST is delegated to the IANA tz database (``zoneinfo``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, time
from typing import Literal
from zoneinfo import ZoneInfo

from packages.common.instrument_id import Market

UTC = timezone.utc

_CN_TZ = ZoneInfo("Asia/Shanghai")
_US_TZ = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Reject naive datetimes; normalize to UTC."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed; attach a tz")
    return dt.astimezone(UTC)


def to_utc(dt: datetime, assume_tz: ZoneInfo | timezone | None = None) -> datetime:
    if dt.tzinfo is None:
        if assume_tz is None:
            raise ValueError("naive datetime; supply assume_tz to disambiguate")
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class MarketClock:
    market: Market

    @property
    def tz(self) -> ZoneInfo:
        return _CN_TZ if self.market is Market.CN else _US_TZ

    def to_local(self, dt_utc: datetime) -> datetime:
        return ensure_utc(dt_utc).astimezone(self.tz)

    def regular_session(self) -> tuple[time, time]:
        """Simplified regular-session hours (local time).

        The real trading calendar (holidays, half-days) is handled by
        ``packages.calendar_rule``. This is only the *default* window.
        """
        if self.market is Market.CN:
            return time(9, 30), time(15, 0)  # single continuous window (simplified)
        return time(9, 30), time(16, 0)

    def is_regular_session(self, dt_utc: datetime) -> bool:
        local = self.to_local(dt_utc)
        start, end = self.regular_session()
        return start <= local.time() <= end
