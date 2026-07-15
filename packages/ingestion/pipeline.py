"""Daily-bar ingestion pipeline.

Contract:
- Fully idempotent: rerunning the same (source, instrument, start, end) produces
  no duplicates and does not regress the watermark.
- Emits a versioned Golden Dataset row set (returned as list of Bar for now;
  real writer stores to Parquet in ``s3://raw/{dataset}/{market}/...``).
- Every row has calendar_version and rule_version stamped by the adapter.
- Fails closed on any ERROR/CRITICAL DQ finding (§75): the watermark is NOT
  advanced and no rows are handed to the sink.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Protocol

from packages.common.errors import DataConflictError, DataNotReadyError
from packages.common.instrument_id import InstrumentId
from packages.data_quality.checks import BarChecks, DQFinding, has_errors
from packages.data_sources.contracts import Bar, MarketDataAdapter
from packages.ingestion.watermark import Watermark, WatermarkStore


DATASET_BARS_DAILY = "bars.daily"


class BarSink(Protocol):
    """Anything that can accept a batch of bars. Concrete implementations:
    :class:`packages.data_sources.sql_bar_repo.SqlBarRepository` (via a
    thin adapter that calls ``.upsert_many``), or a plain ``list[Bar]``.
    """
    def write(self, bars: Iterable[Bar]) -> int: ...


@dataclass
class ListSink:
    """Test-friendly sink that appends into an in-memory list."""
    bars: list[Bar] = field(default_factory=list)

    def write(self, bars: Iterable[Bar]) -> int:
        added = 0
        for b in bars:
            self.bars.append(b); added += 1
        return added


@dataclass
class SqlBarSink:
    """Adapter so :class:`SqlBarRepository` matches the :class:`BarSink` protocol."""
    repo: object  # SqlBarRepository — untyped to avoid import cycle

    def write(self, bars: Iterable[Bar]) -> int:
        return self.repo.upsert_many(list(bars))


@dataclass
class IngestReport:
    instrument_id: InstrumentId
    source: str
    written: int = 0
    skipped_by_watermark: int = 0
    findings: list[DQFinding] = field(default_factory=list)
    watermark_before: date | None = None
    watermark_after: date | None = None
    dq_blocked: bool = False


def ingest_bars_daily(
    adapter: MarketDataAdapter,
    instrument_id: InstrumentId,
    start: date,
    end: date,
    *,
    watermarks: WatermarkStore,
    sink: "list[Bar] | BarSink | None" = None,
    strict: bool = True,
) -> IngestReport:
    """Fetch [start, end] daily bars, drop rows <= watermark, DQ-check, emit.

    When ``strict`` is True (the default) any ERROR/CRITICAL DQ finding
    aborts the write: the sink is not touched and the watermark is not
    advanced. The report still returns the offending findings so callers
    can surface them.
    """
    report = IngestReport(instrument_id=instrument_id, source=adapter.adapter_id)
    prev = watermarks.get(DATASET_BARS_DAILY, instrument_id.canonical(), adapter.adapter_id)
    report.watermark_before = prev.last_market_local_date if prev else None

    kept: list[Bar] = []
    seen_dates: set[date] = set()

    for bar in adapter.fetch_bars_daily(instrument_id, start, end):
        # Watermark: only accept strictly greater dates
        if prev and prev.last_market_local_date and bar.market_local_date <= prev.last_market_local_date:
            report.skipped_by_watermark += 1
            continue
        if bar.market_local_date in seen_dates:
            raise DataConflictError(
                f"duplicate bar for {instrument_id.canonical()} @ {bar.market_local_date} from {adapter.adapter_id}"
            )
        seen_dates.add(bar.market_local_date)
        kept.append(bar)

    # DQ
    report.findings.extend(BarChecks().run(kept))

    # Fail-closed: any ERROR/CRITICAL blocks the write + watermark advance.
    if strict and has_errors(report.findings):
        report.dq_blocked = True
        report.written = 0
        report.watermark_after = report.watermark_before
        return report

    # Sink dispatch — accepts either the legacy list[Bar] contract or a
    # protocol-shaped writer.
    if sink is not None:
        if isinstance(sink, list):
            sink.extend(kept)
        else:
            sink.write(kept)
    report.written = len(kept)

    if kept:
        latest = max(b.market_local_date for b in kept)
        watermarks.advance(Watermark(
            dataset=DATASET_BARS_DAILY,
            instrument_key=instrument_id.canonical(),
            source=adapter.adapter_id,
            last_market_local_date=latest,
        ))
        report.watermark_after = latest
    else:
        report.watermark_after = report.watermark_before
    return report
