"""Trading calendar. Phase 1 uses simple weekday + fixed holiday lists.

Real implementation should be replaced with ``exchange_calendars`` (XSHG/XNYS)
under the same interface. Version bumps are recorded via ``calendar_version()``.

DAY GRANULARITY. Intraday bar sessions are handled elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from packages.common.instrument_id import Market


CALENDAR_VERSION = "cal-v0.1"


def calendar_version() -> str:
    return CALENDAR_VERSION


# Minimal seed set. Real deployment MUST replace with an authoritative source.
_CN_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),  # Spring Festival (illustrative)
    date(2026, 4, 6),   # Qingming
    date(2026, 5, 1),   # Labour
    date(2026, 6, 22),  # Dragon Boat
    date(2026, 9, 25),  # Mid-Autumn
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8),
}

_US_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),   # New Year
    date(2026, 1, 19),  # MLK Day (3rd Mon Jan)
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # July 4 observed
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


@dataclass(frozen=True)
class TradingCalendar:
    market: Market
    holidays: frozenset[date]

    def is_session(self, d: date) -> bool:
        if d.weekday() >= 5:  # Sat/Sun
            return False
        return d not in self.holidays

    def next_session(self, d: date) -> date:
        cur = d + timedelta(days=1)
        while not self.is_session(cur):
            cur += timedelta(days=1)
        return cur

    def previous_session(self, d: date) -> date:
        cur = d - timedelta(days=1)
        while not self.is_session(cur):
            cur -= timedelta(days=1)
        return cur

    def sessions_between(self, start: date, end: date) -> list[date]:
        out: list[date] = []
        cur = start
        while cur <= end:
            if self.is_session(cur):
                out.append(cur)
            cur += timedelta(days=1)
        return out


@lru_cache(maxsize=4)
def get_calendar(market: Market) -> TradingCalendar:
    if market is Market.CN:
        return TradingCalendar(Market.CN, frozenset(_CN_HOLIDAYS_2026))
    return TradingCalendar(Market.US, frozenset(_US_HOLIDAYS_2026))
