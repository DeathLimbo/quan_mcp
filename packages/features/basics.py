"""Built-in features. All operate on a chronological ``list[Bar]`` window
ending at (and *including*) the as-of bar. PIT is the caller's job:
the training pipeline slices bars by ``available_at_utc <= as_of_time_utc``
BEFORE calling any feature function.
"""
from __future__ import annotations

import math
from decimal import Decimal

from packages.data_sources.contracts import Bar
from packages.features.registry import feature


def _closes(bars: list[Bar]) -> list[float]:
    return [float(b.close) for b in bars]


@feature("ret_1d", lookback_days=2)
def ret_1d(bars: list[Bar]) -> float | None:
    if len(bars) < 2:
        return None
    c = _closes(bars[-2:])
    if c[0] == 0:
        return None
    return c[1] / c[0] - 1.0


@feature("ret_5d", lookback_days=6)
def ret_5d(bars: list[Bar]) -> float | None:
    if len(bars) < 6:
        return None
    c = _closes(bars)
    if c[-6] == 0:
        return None
    return c[-1] / c[-6] - 1.0


@feature("ret_20d", lookback_days=21)
def ret_20d(bars: list[Bar]) -> float | None:
    if len(bars) < 21:
        return None
    c = _closes(bars)
    if c[-21] == 0:
        return None
    return c[-1] / c[-21] - 1.0


@feature("vol_20d", lookback_days=21)
def vol_20d(bars: list[Bar]) -> float | None:
    if len(bars) < 21:
        return None
    c = _closes(bars[-21:])
    rets = [c[i] / c[i - 1] - 1.0 for i in range(1, len(c)) if c[i - 1] != 0]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


@feature("mom_60d_minus_5d", lookback_days=61)
def mom_60_5(bars: list[Bar]) -> float | None:
    """Classic momentum: t-60 to t-5 return, skipping short reversal window."""
    if len(bars) < 61:
        return None
    c = _closes(bars)
    denom = c[-61]
    if denom == 0:
        return None
    return c[-6] / denom - 1.0


@feature("dollar_vol_20d", lookback_days=20)
def dollar_vol_20d(bars: list[Bar]) -> float | None:
    if len(bars) < 20:
        return None
    tvs = [float(b.close) * float(b.volume) for b in bars[-20:]]
    return sum(tvs) / len(tvs)
