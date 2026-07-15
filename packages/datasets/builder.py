"""Build (features, label) rows for one instrument, PIT-safe."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Sequence

from packages.data_sources.contracts import Bar
from packages.features import FeatureSet
from packages.labels import forward_return


@dataclass(frozen=True, slots=True)
class DatasetRow:
    as_of_date: date
    features: dict[str, float | None]
    label: float | None
    feature_set_hash: str


def build_dataset(
    bars: Sequence[Bar],
    feature_names: Sequence[str],
    *,
    horizon_days: int,
    start: date,
    end: date,
) -> list[DatasetRow]:
    fs = FeatureSet(tuple(feature_names))
    bars_sorted = sorted(bars, key=lambda b: b.market_local_date)
    rows: list[DatasetRow] = []
    for i, b in enumerate(bars_sorted):
        if b.market_local_date < start or b.market_local_date > end:
            continue
        as_of_utc = b.available_at_utc  # PIT: only rows visible at this moment
        feats = fs.compute(bars_sorted, as_of_utc)
        y = forward_return(list(bars_sorted), i, horizon_days)
        rows.append(DatasetRow(
            as_of_date=b.market_local_date,
            features=feats,
            label=y,
            feature_set_hash=fs.content_hash,
        ))
    return rows
