"""Performance and calibration metrics. Pure Python, no numpy dep."""
from __future__ import annotations

import math
from typing import Sequence


def _pct_returns_from_equity(equity: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(equity)):
        if equity[i - 1] == 0:
            continue
        out.append(equity[i] / equity[i - 1] - 1.0)
    return out


def sharpe_ratio(returns: Sequence[float], *, periods_per_year: int = 252, rf: float = 0.0) -> float:
    """Annualized Sharpe. Returns 0 if fewer than 2 samples or std is 0."""
    r = list(returns)
    if len(r) < 2:
        return 0.0
    excess = [x - rf / periods_per_year for x in r]
    m = sum(excess) / len(excess)
    var = sum((x - m) ** 2 for x in excess) / (len(excess) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return (m / sd) * math.sqrt(periods_per_year)


def max_drawdown(equity: Sequence[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        if peak > 0 and e < peak:
            dd = e / peak - 1.0  # negative
            if dd < mdd:
                mdd = dd
    return mdd


def hit_rate(returns: Sequence[float]) -> float:
    r = [x for x in returns if x != 0]
    if not r:
        return 0.0
    return sum(1 for x in r if x > 0) / len(r)


def information_coefficient(scores: Sequence[float], forward_returns: Sequence[float]) -> float:
    """Spearman rank correlation (pure Python, ties -> average rank)."""
    if len(scores) != len(forward_returns) or len(scores) < 2:
        return 0.0

    def _rank(xs: Sequence[float]) -> list[float]:
        idx = sorted(range(len(xs)), key=lambda i: xs[i])
        ranks = [0.0] * len(xs)
        i = 0
        while i < len(xs):
            j = i
            while j + 1 < len(xs) and xs[idx[j + 1]] == xs[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[idx[k]] = avg
            i = j + 1
        return ranks

    rx = _rank(scores)
    ry = _rank(forward_returns)
    n = len(rx)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    denx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    deny = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx * deny)


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probs) != len(outcomes) or not probs:
        return 0.0
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


def isotonic_calibrate(probs: Sequence[float], outcomes: Sequence[int]) -> list[tuple[float, float]]:
    """Pool-Adjacent-Violators isotonic regression.
    Returns sorted (raw_prob, calibrated_prob) pairs.
    """
    if not probs:
        return []
    order = sorted(range(len(probs)), key=lambda i: probs[i])
    ys = [float(outcomes[i]) for i in order]
    xs = [float(probs[i]) for i in order]
    # PAVA
    weights = [1.0] * len(ys)
    values = list(ys)
    i = 0
    while i < len(values) - 1:
        if values[i] > values[i + 1]:
            w_new = weights[i] + weights[i + 1]
            v_new = (values[i] * weights[i] + values[i + 1] * weights[i + 1]) / w_new
            values[i] = v_new
            weights[i] = w_new
            del values[i + 1]
            del weights[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    # Re-expand
    out: list[tuple[float, float]] = []
    idx = 0
    for w_block, v_block in zip(weights, values):
        for _ in range(int(w_block)):
            out.append((xs[idx], v_block))
            idx += 1
    return out
