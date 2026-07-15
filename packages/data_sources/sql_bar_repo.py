"""SQLAlchemy Core-backed bar repository.

Mirrors the schema in migration ``0003_market_bar``. Reads and writes are
kept idempotent by (instrument_id, market_local_date, source) primary key,
matching the ingestion pipeline's watermark semantics.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue, parse_instrument_id,
)
from packages.data_sources.contracts import Bar

# ---------------------------------------------------------------------
# Schema (mirrors migration 0003_market_bar). Kept without FK to
# ``instruments`` so tests can create the table alone.
# ---------------------------------------------------------------------
metadata = sa.MetaData()

market_bar_table = sa.Table(
    "market_bar", metadata,
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("market_local_date", sa.Date, nullable=False),
    sa.Column("event_time_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("open", sa.Numeric(20, 6), nullable=False),
    sa.Column("high", sa.Numeric(20, 6), nullable=False),
    sa.Column("low", sa.Numeric(20, 6), nullable=False),
    sa.Column("close", sa.Numeric(20, 6), nullable=False),
    sa.Column("volume", sa.Numeric(24, 4), nullable=False),
    sa.Column("turnover", sa.Numeric(24, 4)),
    sa.Column("adj_factor", sa.Numeric(20, 10)),
    sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("calendar_version", sa.Text, nullable=False),
    sa.Column("rule_version", sa.Text, nullable=False),
    sa.Column("source_version", sa.Text, nullable=False,
              server_default=sa.text("'unspecified'")),
    sa.Column("license_tag", sa.Text, nullable=False,
              server_default=sa.text("'INTERNAL_RESEARCH'")),
    sa.Column("quality_status", sa.Text, nullable=False,
              server_default=sa.text("'NORMAL'")),
    sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("instrument_id", "market_local_date", "source"),
    sa.CheckConstraint("available_at_utc >= event_time_utc", name="ck_market_bar_pit"),
    sa.CheckConstraint("high >= low", name="ck_market_bar_range"),
)


class SqlBarRepository:
    """Idempotent write / point-in-time read for daily OHLCV bars."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- write side ----
    def upsert_many(self, bars: Iterable[Bar]) -> int:
        """Delete-then-insert per (id, date, source). Returns rows written."""
        rows: list[dict] = []
        pk_triples: list[tuple[str, date, str]] = []
        now = datetime.now(timezone.utc)
        for b in bars:
            iid = b.instrument_id.canonical()
            rows.append({
                "instrument_id": iid,
                "market_local_date": b.market_local_date,
                "event_time_utc": b.event_time_utc,
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "turnover": b.turnover,
                "adj_factor": b.adj_factor,
                "available_at_utc": b.available_at_utc,
                "source": b.source,
                "calendar_version": b.calendar_version,
                "rule_version": b.rule_version,
                "source_version": getattr(b, "source_version", "unspecified"),
                "license_tag": getattr(b, "license_tag", "INTERNAL_RESEARCH"),
                "quality_status": getattr(b, "quality_status", "NORMAL"),
                "ingested_at_utc": now,
            })
            pk_triples.append((iid, b.market_local_date, b.source))
        if not rows:
            return 0
        with self._engine.begin() as conn:
            for iid, d, src in pk_triples:
                conn.execute(sa.delete(market_bar_table)
                             .where(market_bar_table.c.instrument_id == iid)
                             .where(market_bar_table.c.market_local_date == d)
                             .where(market_bar_table.c.source == src))
            conn.execute(sa.insert(market_bar_table), rows)
        return len(rows)

    # ---- read side ----
    def find_range(
        self,
        instrument_id: InstrumentId,
        start: date,
        end: date,
        *,
        source: str | None = None,
        as_of_utc: datetime | None = None,
    ) -> Sequence[Bar]:
        """Return bars for [start, end] filtered by PIT ``available_at_utc``.

        Passing ``as_of_utc`` enforces point-in-time reads: only bars that
        were available at or before this timestamp are returned. This is
        the standard defense against look-ahead bias in backtests.
        """
        conds = [
            market_bar_table.c.instrument_id == instrument_id.canonical(),
            market_bar_table.c.market_local_date >= start,
            market_bar_table.c.market_local_date <= end,
        ]
        if source:
            conds.append(market_bar_table.c.source == source)
        if as_of_utc is not None:
            conds.append(market_bar_table.c.available_at_utc <= as_of_utc)
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(market_bar_table)
                .where(sa.and_(*conds))
                .order_by(market_bar_table.c.market_local_date.asc(),
                          market_bar_table.c.source.asc())
            ).mappings().all()
        return [_row_to_bar(r) for r in rows]

    def latest(
        self,
        instrument_id: InstrumentId,
        *,
        source: str | None = None,
        as_of_utc: datetime | None = None,
    ) -> Bar | None:
        conds = [market_bar_table.c.instrument_id == instrument_id.canonical()]
        if source:
            conds.append(market_bar_table.c.source == source)
        if as_of_utc is not None:
            conds.append(market_bar_table.c.available_at_utc <= as_of_utc)
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(market_bar_table)
                .where(sa.and_(*conds))
                .order_by(market_bar_table.c.market_local_date.desc())
                .limit(1)
            ).mappings().first()
        return _row_to_bar(row) if row else None


def _row_to_bar(row) -> Bar:
    iid = parse_instrument_id(row["instrument_id"])
    event = row["event_time_utc"]
    available = row["available_at_utc"]
    if event is not None and event.tzinfo is None:
        event = event.replace(tzinfo=timezone.utc)
    if available is not None and available.tzinfo is None:
        available = available.replace(tzinfo=timezone.utc)
    return Bar(
        instrument_id=iid,
        event_time_utc=event,
        market_local_date=row["market_local_date"],
        open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
        turnover=Decimal(str(row["turnover"])) if row["turnover"] is not None else None,
        adj_factor=Decimal(str(row["adj_factor"])) if row["adj_factor"] is not None else None,
        available_at_utc=available,
        source=row["source"],
        calendar_version=row["calendar_version"],
        rule_version=row["rule_version"],
        source_version=row.get("source_version") or "unspecified",
        license_tag=row.get("license_tag") or "INTERNAL_RESEARCH",
        quality_status=row.get("quality_status") or "NORMAL",
    )
