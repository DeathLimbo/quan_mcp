"""SQLAlchemy Core-backed InstrumentRepository.

Mirrors the schema defined by migration ``0001_instruments``. We declare
lightweight ``Table`` objects at module level so tests can create the
tables in-memory (SQLite) via ``metadata.create_all(engine)`` without
running the full Alembic upgrade. Production still runs the migration —
this module NEVER writes DDL and only executes DML.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue, parse_instrument_id,
)
from packages.common.time_utils import utcnow
from packages.data_sources.contracts import InstrumentDescriptor
from packages.instruments.service import (
    InstrumentRecord, InstrumentRepository,
)

# ---------------------------------------------------------------------
# Schema (kept in sync with migration 0001_instruments).
# ---------------------------------------------------------------------
metadata = sa.MetaData()

instruments_table = sa.Table(
    "instruments", metadata,
    sa.Column("instrument_id", sa.Text, primary_key=True),
    sa.Column("market", sa.Text, nullable=False),
    sa.Column("venue", sa.Text, nullable=False),
    sa.Column("asset_type", sa.Text, nullable=False),
    sa.Column("symbol", sa.Text, nullable=False),
    sa.Column("name_local", sa.Text),
    sa.Column("name_en", sa.Text),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("lot_size", sa.Integer),
    sa.Column("first_trade_date", sa.Date),
    sa.Column("last_trade_date", sa.Date),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("calendar_version", sa.Text, nullable=False),
    sa.Column("rule_version", sa.Text, nullable=False),
)

aliases_table = sa.Table(
    "instrument_aliases", metadata,
    sa.Column("alias", sa.Text, nullable=False),
    sa.Column("alias_source", sa.Text, nullable=False),
    sa.Column("instrument_id", sa.Text,
              sa.ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
              nullable=False),
    sa.Column("valid_from_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("valid_to_utc", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("alias", "alias_source", "valid_from_utc"),
)


class SqlInstrumentRepository(InstrumentRepository):
    """SQLAlchemy-backed repository. Idempotent upsert-by-primary-key."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- write side ----
    def upsert(self, rec: InstrumentRecord) -> InstrumentRecord:
        desc = rec.descriptor
        iid = desc.instrument_id
        ingested = rec.ingested_at_utc or utcnow()
        stored = InstrumentRecord(
            descriptor=desc, aliases=rec.aliases,
            ingested_at_utc=ingested,
            calendar_version=rec.calendar_version,
            rule_version=rec.rule_version,
        )
        payload = {
            "instrument_id": iid.canonical(),
            "market": iid.market.value,
            "venue": iid.venue.value,
            "asset_type": iid.asset_type.value,
            "symbol": iid.symbol,
            "name_local": desc.name_local,
            "name_en": desc.name_en,
            "currency": desc.currency,
            "lot_size": desc.lot_size,
            "first_trade_date": desc.first_trade_date,
            "last_trade_date": desc.last_trade_date,
            "status": desc.status,
            "ingested_at_utc": ingested,
            "calendar_version": rec.calendar_version,
            "rule_version": rec.rule_version,
        }
        with self._engine.begin() as conn:
            # Portable "upsert": delete-then-insert is fine because PK is
            # the canonical id and rows are keyed 1:1. Prod Postgres path
            # can be swapped to ON CONFLICT (instrument_id) DO UPDATE.
            conn.execute(sa.delete(instruments_table)
                         .where(instruments_table.c.instrument_id == iid.canonical()))
            conn.execute(sa.insert(instruments_table).values(**payload))
        return stored

    def add_alias(self, alias: str, *, source: str, target: InstrumentId,
                  valid_from_utc: datetime, valid_to_utc: datetime | None = None) -> None:
        with self._engine.begin() as conn:
            conn.execute(sa.insert(aliases_table).values(
                alias=alias, alias_source=source,
                instrument_id=target.canonical(),
                valid_from_utc=valid_from_utc,
                valid_to_utc=valid_to_utc,
            ))

    # ---- read side ----
    def get(self, iid: InstrumentId) -> InstrumentRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(instruments_table)
                .where(instruments_table.c.instrument_id == iid.canonical())
            ).mappings().first()
        if row is None:
            return None
        return self._row_to_record(row)

    def resolve_alias(self, alias: str, *, source: str,
                      as_of: datetime | None = None) -> InstrumentRecord | None:
        as_of = as_of or utcnow()
        with self._engine.connect() as conn:
            j = aliases_table.join(
                instruments_table,
                aliases_table.c.instrument_id == instruments_table.c.instrument_id,
            )
            stmt = (sa.select(instruments_table)
                    .select_from(j)
                    .where(aliases_table.c.alias == alias)
                    .where(aliases_table.c.alias_source == source)
                    .where(aliases_table.c.valid_from_utc <= as_of)
                    .where(sa.or_(aliases_table.c.valid_to_utc.is_(None),
                                  aliases_table.c.valid_to_utc > as_of))
                    .order_by(aliases_table.c.valid_from_utc.desc())
                    .limit(1))
            row = conn.execute(stmt).mappings().first()
        return self._row_to_record(row) if row else None

    def all(self) -> Sequence[InstrumentRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(sa.select(instruments_table)).mappings().all()
        return [self._row_to_record(r) for r in rows]

    # ---- helpers ----
    @staticmethod
    def _row_to_record(row) -> InstrumentRecord:
        iid = InstrumentId(
            market=Market(row["market"]),
            venue=Venue(row["venue"]),
            asset_type=AssetType(row["asset_type"]),
            symbol=row["symbol"],
        )
        desc = InstrumentDescriptor(
            instrument_id=iid,
            name_local=row["name_local"],
            name_en=row["name_en"],
            currency=row["currency"],
            lot_size=row["lot_size"],
            first_trade_date=row["first_trade_date"],
            last_trade_date=row["last_trade_date"],
            status=row["status"],
        )
        ingested = row["ingested_at_utc"]
        # SQLite returns naive datetimes even for TIMESTAMP(tz=True) columns.
        if ingested is not None and ingested.tzinfo is None:
            ingested = ingested.replace(tzinfo=timezone.utc)
        return InstrumentRecord(
            descriptor=desc, aliases=(),
            ingested_at_utc=ingested,
            calendar_version=row["calendar_version"],
            rule_version=row["rule_version"],
        )
