"""Forward-return labels (H-day)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from packages.data_sources.contracts import Bar


@dataclass(frozen=True, slots=True)
class ForwardReturnLabel:
    market_local_date: date
    horizon_days: int
    value: float | None            # None if horizon extends past available data
    binary: int | None             # 1 if value > threshold else 0


def forward_return(bars: list[Bar], t_index: int, horizon_days: int) -> float | None:
    if t_index < 0 or t_index + horizon_days >= len(bars):
        return None
    c0 = float(bars[t_index].close)
    c1 = float(bars[t_index + horizon_days].close)
    if c0 == 0:
        return None
    return c1 / c0 - 1.0


def forward_return_binary(
    bars: list[Bar], t_index: int, horizon_days: int, threshold: float = 0.0
) -> int | None:
    r = forward_return(bars, t_index, horizon_days)
    if r is None:
        return None
    return 1 if r > threshold else 0
