"""Fundamentals (spec §6.9, §PIT).

A ``Fact`` is a point-in-time snapshot of a fundamental metric. Facts are
versioned by ``as_of_utc`` (when the value became knowable) and
``available_at_utc`` (when the value became consumable). Callers query with
an ``as_of`` cutoff and get the last row satisfying ``available_at_utc <=
as_of`` — no future leakage possible.
"""
from packages.fundamentals.facts import (
    Fact, FactName, FactStore, PitQuery, latest_as_of,
)

__all__ = ["Fact", "FactName", "FactStore", "PitQuery", "latest_as_of"]
