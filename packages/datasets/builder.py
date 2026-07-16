"""Build (features, label) rows for one instrument, PIT-safe."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Callable, Sequence

from packages.data_sources.contracts import Bar
from packages.features import FeatureSet, FundamentalContext
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
    fund_ctx_provider: Callable[[date, "InstrumentId"], FundamentalContext | None] | None = None,
) -> list[DatasetRow]:
    """Build (features, label) rows for one instrument, PIT-safe.

    ``fund_ctx_provider(as_of_date, instrument_id)`` optionally supplies a
    PIT-safe :class:`FundamentalContext` per row so features declared with
    ``requires_fundamentals=True`` can be computed. When None (or when the
    provider returns None for a given date), those features resolve to None
    (fail-closed) and the row is still emitted.
    """
    fs = FeatureSet(tuple(feature_names))
    bars_sorted = sorted(bars, key=lambda b: b.market_local_date)
    rows: list[DatasetRow] = []
    for i, b in enumerate(bars_sorted):
        if b.market_local_date < start or b.market_local_date > end:
            continue
        as_of_utc = b.available_at_utc  # PIT: only rows visible at this moment
        fund_ctx = None
        if fund_ctx_provider is not None:
            fund_ctx = fund_ctx_provider(b.market_local_date, b.instrument_id)
        feats = fs.compute(bars_sorted, as_of_utc, fund_ctx=fund_ctx)
        y = forward_return(list(bars_sorted), i, horizon_days)
        rows.append(DatasetRow(
            as_of_date=b.market_local_date,
            features=feats,
            label=y,
            feature_set_hash=fs.content_hash,
        ))
    return rows
