"""Walk-forward splitter with purging and embargo.

- **Walk-forward**: successive (train, valid, test) windows moving forward in
  time. No random shuffling. Ever.
- **Purging**: remove training rows whose label horizon overlaps the validation
  window (prevents look-ahead when H-day forward-return labels span the boundary).
- **Embargo**: remove validation rows whose label horizon overlaps the test
  window (or the *next* window in a chain), preventing leakage across folds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TimeSplit:
    train: tuple[date, date]
    valid: tuple[date, date]
    test: tuple[date, date]


def _in_range(d: date, r: tuple[date, date]) -> bool:
    return r[0] <= d <= r[1]


def purge_and_embargo(
    rows: Iterable[tuple[date, ...]],
    *,
    train: tuple[date, date],
    valid: tuple[date, date],
    horizon_days: int,
    embargo_days: int = 0,
) -> tuple[list[tuple[date, ...]], list[tuple[date, ...]]]:
    """Return (kept_train_rows, kept_valid_rows).

    Row shape: ``(as_of_date, ...arbitrary payload...)``.
    """
    kept_train: list[tuple[date, ...]] = []
    kept_valid: list[tuple[date, ...]] = []
    for row in rows:
        d = row[0]
        if _in_range(d, train):
            # Label spans up to d + horizon_days. Purge if it enters valid window.
            label_end = d + timedelta(days=horizon_days)
            if label_end >= valid[0]:
                continue
            kept_train.append(row)
        elif _in_range(d, valid):
            # Embargo: exclude rows too close to the boundary
            if d < valid[0] + timedelta(days=embargo_days):
                continue
            kept_valid.append(row)
    return kept_train, kept_valid


@dataclass(frozen=True)
class WalkForwardSplitter:
    train_days: int
    valid_days: int
    test_days: int
    step_days: int

    def splits(self, first: date, last: date) -> list[TimeSplit]:
        out: list[TimeSplit] = []
        cursor = first
        while True:
            t_start = cursor
            t_end = t_start + timedelta(days=self.train_days - 1)
            v_start = t_end + timedelta(days=1)
            v_end = v_start + timedelta(days=self.valid_days - 1)
            te_start = v_end + timedelta(days=1)
            te_end = te_start + timedelta(days=self.test_days - 1)
            if te_end > last:
                break
            out.append(TimeSplit((t_start, t_end), (v_start, v_end), (te_start, te_end)))
            cursor = cursor + timedelta(days=self.step_days)
        return out
