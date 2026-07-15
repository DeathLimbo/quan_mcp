"""Pure drift statistics.

No numpy dependency — the whole system stays importable without scientific
extras. If numpy is available, callers can pass numpy arrays; the code works
on any :class:`typing.Sequence[float]`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


class DriftLevel(str, Enum):
    OK = "ok"
    WATCH = "watch"       # gentle warning; keep predicting but log
    ALERT = "alert"       # de-weight / shadow-only
    HALT = "halt"         # trip SAFE_MODE / NO_FORECAST


# ---- PSI --------------------------------------------------------------------

def _histogram(values: Sequence[float], edges: Sequence[float]) -> list[float]:
    counts = [0] * (len(edges) - 1)
    for v in values:
        # right-open bins except last
        placed = False
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            if lo <= v < hi:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    total = sum(counts) or 1
    return [c / total for c in counts]


def _uniform_edges(sample: Sequence[float], n_bins: int) -> list[float]:
    if not sample:
        return [0.0] * (n_bins + 1)
    lo, hi = min(sample), max(sample)
    if lo == hi:
        # degenerate: single value; make bins around it
        return [lo - 0.5 + i / n_bins for i in range(n_bins + 1)]
    step = (hi - lo) / n_bins
    return [lo + i * step for i in range(n_bins + 1)]


def psi(baseline: Sequence[float], current: Sequence[float], *, n_bins: int = 10,
        eps: float = 1e-6) -> float:
    """Population Stability Index.

    Rules of thumb (per industry consensus, spec §89):
    - PSI < 0.10 → OK
    - 0.10–0.25 → moderate drift (WATCH)
    - > 0.25    → significant drift (ALERT)
    """
    if not baseline or not current:
        raise ValueError("baseline and current must be non-empty")
    edges = _uniform_edges(baseline, n_bins)
    p = _histogram(baseline, edges)
    q = _histogram(current, edges)
    total = 0.0
    for pi, qi in zip(p, q):
        pi_ = max(pi, eps)
        qi_ = max(qi, eps)
        total += (qi_ - pi_) * math.log(qi_ / pi_)
    return total


def psi_level(value: float) -> DriftLevel:
    if value < 0.10:
        return DriftLevel.OK
    if value < 0.25:
        return DriftLevel.WATCH
    if value < 0.50:
        return DriftLevel.ALERT
    return DriftLevel.HALT


# ---- KS ---------------------------------------------------------------------

def ks_stat(baseline: Sequence[float], current: Sequence[float]) -> float:
    """Two-sample KS statistic (sup |F_A - F_B|)."""
    a = sorted(baseline)
    b = sorted(current)
    if not a or not b:
        raise ValueError("baseline and current must be non-empty")
    i = j = 0
    d = 0.0
    while i < len(a) and j < len(b):
        av, bv = a[i], b[j]
        if av <= bv:
            i += 1
        if bv <= av:
            j += 1
        d = max(d, abs(i / len(a) - j / len(b)))
    return d


def ks_level(value: float) -> DriftLevel:
    if value < 0.05:
        return DriftLevel.OK
    if value < 0.10:
        return DriftLevel.WATCH
    if value < 0.20:
        return DriftLevel.ALERT
    return DriftLevel.HALT


# ---- OOD share --------------------------------------------------------------

def ood_share(flags: Iterable[bool]) -> float:
    xs = list(flags)
    if not xs:
        return 0.0
    return sum(1 for x in xs if x) / len(xs)


def ood_level(share: float) -> DriftLevel:
    if share < 0.02:
        return DriftLevel.OK
    if share < 0.05:
        return DriftLevel.WATCH
    if share < 0.10:
        return DriftLevel.ALERT
    return DriftLevel.HALT


# ---- prediction distribution shift (KL) ------------------------------------

def prediction_shift_kl(baseline: Sequence[float], current: Sequence[float],
                        *, n_bins: int = 10, eps: float = 1e-6) -> float:
    """Discretised KL(current || baseline) on [0, 1] score histograms."""
    edges = [i / n_bins for i in range(n_bins + 1)]
    p = _histogram(baseline, edges)
    q = _histogram(current, edges)
    total = 0.0
    for pi, qi in zip(p, q):
        pi_ = max(pi, eps)
        qi_ = max(qi, eps)
        total += qi_ * math.log(qi_ / pi_)
    return total


# ---- rolling IC (effect drift) ---------------------------------------------

def _rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    ra = _rank(a)
    rb = _rank(b)
    n = len(a)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    num = sum((ra[i] - mean_a) * (rb[i] - mean_b) for i in range(n))
    da = math.sqrt(sum((r - mean_a) ** 2 for r in ra))
    db = math.sqrt(sum((r - mean_b) ** 2 for r in rb))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def ic_series(pairs: Sequence[tuple[Sequence[float], Sequence[float]]]) -> list[float]:
    """Rolling Spearman IC per rebalance snapshot."""
    return [_spearman(pred, actual) for pred, actual in pairs]


def ic_trend_level(ics: Sequence[float], *, window: int = 5,
                   deterioration: float = -0.02) -> DriftLevel:
    """ALERT if the trailing mean IC drops below ``deterioration`` for N in a row."""
    if len(ics) < window:
        return DriftLevel.OK
    trailing = sum(ics[-window:]) / window
    if trailing < deterioration * 2:
        return DriftLevel.HALT
    if trailing < deterioration:
        return DriftLevel.ALERT
    if trailing < 0:
        return DriftLevel.WATCH
    return DriftLevel.OK


# ---- roll-up ---------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DriftReport:
    feature_psi: dict[str, float] = field(default_factory=dict)
    feature_ks: dict[str, float] = field(default_factory=dict)
    ood_share: float = 0.0
    prediction_kl: float = 0.0
    rolling_ic: list[float] = field(default_factory=list)

    def worst_level(self) -> DriftLevel:
        levels = [
            *(psi_level(v) for v in self.feature_psi.values()),
            *(ks_level(v) for v in self.feature_ks.values()),
            ood_level(self.ood_share),
            ic_trend_level(self.rolling_ic),
        ]
        order = [DriftLevel.OK, DriftLevel.WATCH, DriftLevel.ALERT, DriftLevel.HALT]
        worst = DriftLevel.OK
        for lv in levels:
            if order.index(lv) > order.index(worst):
                worst = lv
        return worst
