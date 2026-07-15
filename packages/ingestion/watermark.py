"""High-water-mark tracking per (dataset, instrument, source)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Watermark:
    dataset: str
    instrument_key: str
    source: str
    last_market_local_date: date | None


class WatermarkStore(Protocol):
    def get(self, dataset: str, instrument_key: str, source: str) -> Watermark | None: ...
    def advance(self, wm: Watermark) -> None: ...


@dataclass
class InMemoryWatermarkStore:
    _store: dict[tuple[str, str, str], Watermark] = field(default_factory=dict)

    def get(self, dataset: str, instrument_key: str, source: str) -> Watermark | None:
        return self._store.get((dataset, instrument_key, source))

    def advance(self, wm: Watermark) -> None:
        key = (wm.dataset, wm.instrument_key, wm.source)
        prev = self._store.get(key)
        if prev and prev.last_market_local_date and wm.last_market_local_date \
                and wm.last_market_local_date < prev.last_market_local_date:
            # Watermarks are monotone; refuse regression.
            raise ValueError(
                f"watermark regression for {key}: {wm.last_market_local_date} < {prev.last_market_local_date}"
            )
        self._store[key] = wm
