"""Technical / cross-sectional features derivable from bars alone — spec §12.2.

These complement ``basics.py`` (return / volume momentum) with oscillators,
volatility-percentile, drawdown and price-relative-to-MA features. All are
PIT-safe: they only consume the bar window already filtered by
:class:`FeatureSet.compute` to ``available_at_utc <= as_of``.

Fundamentals-based features (PE / PB / market-cap / sector-relative) require
extending the feature-fn signature beyond ``bars`` and are deferred.
"""
from __future__ import annotations

import math
from typing import Sequence

from packages.data_sources.contracts import Bar
from packages.features.registry import feature


def _closes(bars: Sequence[Bar]) -> list[float]:
    return [float(b.close) for b in bars]


def _highs(bars: Sequence[Bar]) -> list[float]:
    return [float(b.high) for b in bars]


def _lows(bars: Sequence[Bar]) -> list[float]:
    return [float(b.low) for b in bars]


def _volumes(bars: Sequence[Bar]) -> list[float]:
    return [float(b.volume) for b in bars]


@feature("rsi_14d", lookback_days=15)
def rsi_14d(bars: Sequence[Bar]) -> float | None:
    """14-period Relative Strength Index (Wilder smoothing)."""
    if len(bars) < 15:
        return None
    closes = _closes(bars)
    gains, losses = 0.0, 0.0
    for i in range(1, 15):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / 14
    avg_loss = losses / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@feature("atr_14d", lookback_days=15)
def atr_14d(bars: Sequence[Bar]) -> float | None:
    """14-period Average True Range as a fraction of last close."""
    if len(bars) < 15:
        return None
    trs: list[float] = []
    for i in range(1, 15):
        h, l = float(bars[i].high), float(bars[i].low)
        pc = float(bars[i - 1].close)
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs) / 14
    last = float(bars[-1].close)
    if last == 0:
        return None
    return atr / last


@feature("vol_pct_rank_20d", lookback_days=21)
def vol_pct_rank_20d(bars: Sequence[Bar]) -> float | None:
    """Percentile rank of the latest volume within the 20-day window [0,1]."""
    if len(bars) < 2:
        return None
    vols = _volumes(bars)
    latest = vols[-1]
    below = sum(1 for v in vols if v < latest)
    return below / (len(vols) - 1) if len(vols) > 1 else 0.5


@feature("price_ma_dev_20d", lookback_days=21)
def price_ma_dev_20d(bars: Sequence[Bar]) -> float | None:
    """Deviation of last close from its 20-day MA, as a fraction of the MA."""
    if len(bars) < 21:
        return None
    closes = _closes(bars)[-21:]
    ma = sum(closes) / 21
    if ma == 0:
        return None
    return (closes[-1] - ma) / ma


@feature("turnover_ratio_5d", lookback_days=6)
def turnover_ratio_5d(bars: Sequence[Bar]) -> float | None:
    """Latest turnover vs 5-day average turnover (>1 = elevated)."""
    if len(bars) < 6:
        return None
    tos = [float(b.turnover) if b.turnover is not None else float(b.volume) * float(b.close)
           for b in bars[-6:]]
    avg = sum(tos[:-1]) / 5
    if avg == 0:
        return None
    return tos[-1] / avg


@feature("max_drawdown_20d", lookback_days=21)
def max_drawdown_20d(bars: Sequence[Bar]) -> float | None:
    """Max drawdown within the 20-day window (positive number, e.g. 0.15 = -15%)."""
    if len(bars) < 2:
        return None
    closes = _closes(bars)[-21:]
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


@feature("ret_skew_20d", lookback_days=21)
def ret_skew_20d(bars: Sequence[Bar]) -> float | None:
    """Skewness of daily returns over 20 days (negative = left-tailed)."""
    if len(bars) < 21:
        return None
    closes = _closes(bars)[-21:]
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0.0
            for i in range(1, len(closes))]
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    if var == 0:
        return 0.0
    std = math.sqrt(var)
    skew = sum((r - mean) ** 3 for r in rets) / (n * std ** 3)
    return skew


@feature("price_52w_high_pct", lookback_days=252)
def price_52w_high_pct(bars: Sequence[Bar]) -> float | None:
    """Current close as a fraction of the 52-week high [0,1]."""
    if len(bars) < 2:
        return None
    highs = _highs(bars)
    hi = max(highs)
    last = float(bars[-1].close)
    if hi == 0:
        return None
    return last / hi


@feature("willr_14d", lookback_days=15)
def willr_14d(bars: Sequence[Bar]) -> float | None:
    """Williams %R over 14 periods, in [-100, 0]."""
    if len(bars) < 15:
        return None
    window = bars[-15:]
    hh = max(_highs(window))
    ll = min(_lows(window))
    last = float(window[-1].close)
    if hh == ll:
        return -50.0
    return ((hh - last) / (hh - ll)) * -100.0
