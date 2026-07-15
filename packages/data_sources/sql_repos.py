"""SQLAlchemy Core repositories for Phase-1 auxiliary tables.

Each repository mirrors the schema in the corresponding Alembic migration
under ``sql/migrations/versions``. They intentionally share one
``metadata`` object so tests can create all tables with a single call:

    from packages.data_sources.sql_repos import metadata
    metadata.create_all(engine)

Foreign keys back to ``instruments`` are omitted so tables can be created
standalone in unit tests (and so ingestion ordering is not blocked by
FK cascades during bulk backfills).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from packages.common.instrument_id import InstrumentId, parse_instrument_id
from packages.data_sources.contracts import CorporateAction, FundNAV
from packages.fundamentals.facts import Fact, FactName

# ---------------------------------------------------------------------
# Shared metadata (kept separate from sql_bar_repo.metadata; this keeps the
# bar-repo tests isolated from these auxiliary tables).
# ---------------------------------------------------------------------
metadata = sa.MetaData()


# ---------- corporate_action ---------- (migration 0004)
corp_action_table = sa.Table(
    "corporate_action", metadata,
    sa.Column("action_id", sa.Text, primary_key=True),
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("action_type", sa.Text, nullable=False),
    sa.Column("announcement_date_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ex_date_local", sa.Date, nullable=False),
    sa.Column("payable_date_local", sa.Date),
    sa.Column("ratio", sa.Numeric(24, 10)),
    sa.Column("currency", sa.Text),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_version", sa.Text, nullable=False,
              server_default=sa.text("'unspecified'")),
    sa.Column("license_tag", sa.Text, nullable=False,
              server_default=sa.text("'INTERNAL_RESEARCH'")),
    sa.Column("quality_status", sa.Text, nullable=False,
              server_default=sa.text("'NORMAL'")),
    sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "action_type IN ('SPLIT','DIVIDEND','MERGER','SPINOFF','RIGHTS')",
        name="ck_ca_type",
    ),
)


def _ca_id(a: CorporateAction) -> str:
    return f"{a.instrument_id.canonical()}|{a.action_type}|{a.ex_date_local.isoformat()}|{a.source}"


class SqlCorporateActionRepository:
    """PIT-aware store for corporate actions.

    Actions are indexed by (instrument, ex_date). Callers filter by
    ``available_at_utc`` to enforce point-in-time.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_many(self, actions: Iterable[CorporateAction]) -> int:
        now = datetime.now(timezone.utc)
        rows: list[dict] = []
        ids: list[str] = []
        for a in actions:
            aid = _ca_id(a)
            ids.append(aid)
            rows.append({
                "action_id": aid,
                "instrument_id": a.instrument_id.canonical(),
                "action_type": a.action_type,
                "announcement_date_utc": a.announcement_date_utc,
                "ex_date_local": a.ex_date_local,
                "payable_date_local": a.payable_date_local,
                "ratio": a.ratio,
                "currency": a.currency,
                "source": a.source,
                "available_at_utc": a.available_at_utc,
                "source_version": getattr(a, "source_version", "unspecified"),
                "license_tag": getattr(a, "license_tag", "INTERNAL_RESEARCH"),
                "quality_status": getattr(a, "quality_status", "NORMAL"),
                "ingested_at_utc": now,
            })
        if not rows:
            return 0
        with self._engine.begin() as conn:
            conn.execute(sa.delete(corp_action_table)
                         .where(corp_action_table.c.action_id.in_(ids)))
            conn.execute(sa.insert(corp_action_table), rows)
        return len(rows)

    def list_for_instrument(
        self,
        instrument_id: InstrumentId,
        *,
        as_of_utc: datetime | None = None,
    ) -> Sequence[CorporateAction]:
        conds = [corp_action_table.c.instrument_id == instrument_id.canonical()]
        if as_of_utc is not None:
            conds.append(corp_action_table.c.available_at_utc <= as_of_utc)
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(corp_action_table)
                .where(sa.and_(*conds))
                .order_by(corp_action_table.c.ex_date_local.asc())
            ).mappings().all()
        return [_row_to_ca(r) for r in rows]


def _row_to_ca(row) -> CorporateAction:
    iid = parse_instrument_id(row["instrument_id"])
    ann = row["announcement_date_utc"]
    avail = row["available_at_utc"]
    if ann is not None and ann.tzinfo is None:
        ann = ann.replace(tzinfo=timezone.utc)
    if avail is not None and avail.tzinfo is None:
        avail = avail.replace(tzinfo=timezone.utc)
    return CorporateAction(
        instrument_id=iid,
        action_type=row["action_type"],
        announcement_date_utc=ann,
        ex_date_local=row["ex_date_local"],
        payable_date_local=row["payable_date_local"],
        ratio=Decimal(str(row["ratio"])) if row["ratio"] is not None else None,
        currency=row["currency"],
        source=row["source"],
        available_at_utc=avail,
        source_version=row.get("source_version") or "unspecified",
        license_tag=row.get("license_tag") or "INTERNAL_RESEARCH",
        quality_status=row.get("quality_status") or "NORMAL",
    )


# ---------- fundamental_fact ---------- (migration 0005)
fundamental_fact_table = sa.Table(
    "fundamental_fact", metadata,
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("fact_name", sa.Text, nullable=False),
    sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("value_num", sa.Numeric(30, 10)),
    sa.Column("value_text", sa.Text),
    sa.Column("unit", sa.Text),
    sa.Column("period_end_local", sa.Date),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("instrument_id", "fact_name", "as_of_utc", "source"),
    sa.CheckConstraint("available_at_utc >= as_of_utc", name="ck_fund_fact_pit"),
)


class SqlFundamentalFactRepository:
    """Point-in-time fact store.

    Reads via ``get_as_of`` return the latest fact whose ``available_at_utc``
    is ``<= as_of``. This matches the semantics of
    :class:`packages.fundamentals.facts.FactStore` but persisted.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_many(self, facts: Iterable[Fact]) -> int:
        now = datetime.now(timezone.utc)
        rows: list[dict] = []
        pks: list[tuple[str, str, datetime, str]] = []
        for f in facts:
            iid = f.instrument_id.canonical()
            pks.append((iid, f.name.value, f.as_of_utc, f.source))
            rows.append({
                "instrument_id": iid,
                "fact_name": f.name.value,
                "as_of_utc": f.as_of_utc,
                "available_at_utc": f.available_at_utc,
                "value_num": f.value,
                "value_text": None,
                "unit": f.currency,
                "period_end_local": f.period_end,
                "source": f.source,
                "ingested_at_utc": now,
            })
        if not rows:
            return 0
        with self._engine.begin() as conn:
            for iid, name, as_of, src in pks:
                conn.execute(
                    sa.delete(fundamental_fact_table)
                    .where(fundamental_fact_table.c.instrument_id == iid)
                    .where(fundamental_fact_table.c.fact_name == name)
                    .where(fundamental_fact_table.c.as_of_utc == as_of)
                    .where(fundamental_fact_table.c.source == src)
                )
            conn.execute(sa.insert(fundamental_fact_table), rows)
        return len(rows)

    def get_as_of(
        self,
        instrument_id: InstrumentId,
        name: FactName,
        as_of: datetime,
    ) -> Fact | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(fundamental_fact_table)
                .where(fundamental_fact_table.c.instrument_id == instrument_id.canonical())
                .where(fundamental_fact_table.c.fact_name == name.value)
                .where(fundamental_fact_table.c.available_at_utc <= as_of)
                .order_by(fundamental_fact_table.c.available_at_utc.desc())
                .limit(1)
            ).mappings().first()
        return _row_to_fact(row) if row else None

    def history(
        self,
        instrument_id: InstrumentId,
        name: FactName,
        *,
        until: datetime | None = None,
    ) -> list[Fact]:
        conds = [
            fundamental_fact_table.c.instrument_id == instrument_id.canonical(),
            fundamental_fact_table.c.fact_name == name.value,
        ]
        if until is not None:
            conds.append(fundamental_fact_table.c.available_at_utc <= until)
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(fundamental_fact_table)
                .where(sa.and_(*conds))
                .order_by(fundamental_fact_table.c.available_at_utc.asc())
            ).mappings().all()
        return [_row_to_fact(r) for r in rows]


def _row_to_fact(row) -> Fact:
    iid = parse_instrument_id(row["instrument_id"])
    as_of = row["as_of_utc"]
    avail = row["available_at_utc"]
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    if avail is not None and avail.tzinfo is None:
        avail = avail.replace(tzinfo=timezone.utc)
    return Fact(
        instrument_id=iid,
        name=FactName(row["fact_name"]),
        period_end=row["period_end_local"] or as_of.date(),
        value=Decimal(str(row["value_num"])) if row["value_num"] is not None else Decimal("0"),
        currency=row["unit"],
        as_of_utc=as_of,
        available_at_utc=avail,
        source=row["source"],
        source_version="",
    )


# ---------- fund_nav_daily ---------- (migration 0006)
fund_nav_table = sa.Table(
    "fund_nav_daily", metadata,
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("market_local_date", sa.Date, nullable=False),
    sa.Column("event_time_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("unit_nav", sa.Numeric(20, 6), nullable=False),
    sa.Column("accum_nav", sa.Numeric(20, 6)),
    sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("source_version", sa.Text, nullable=False,
              server_default=sa.text("'unspecified'")),
    sa.Column("license_tag", sa.Text, nullable=False,
              server_default=sa.text("'INTERNAL_RESEARCH'")),
    sa.Column("quality_status", sa.Text, nullable=False,
              server_default=sa.text("'NORMAL'")),
    sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("instrument_id", "market_local_date", "source"),
    sa.CheckConstraint("available_at_utc >= event_time_utc", name="ck_fund_nav_pit"),
)


class SqlFundNavRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_many(self, navs: Iterable[FundNAV]) -> int:
        now = datetime.now(timezone.utc)
        rows: list[dict] = []
        pks: list[tuple[str, date, str]] = []
        for n in navs:
            iid = n.instrument_id.canonical()
            pks.append((iid, n.market_local_date, n.source))
            rows.append({
                "instrument_id": iid,
                "market_local_date": n.market_local_date,
                "event_time_utc": n.event_time_utc,
                "unit_nav": n.unit_nav,
                "accum_nav": n.accum_nav,
                "available_at_utc": n.available_at_utc,
                "source": n.source,
                "source_version": getattr(n, "source_version", "unspecified"),
                "license_tag": getattr(n, "license_tag", "INTERNAL_RESEARCH"),
                "quality_status": getattr(n, "quality_status", "NORMAL"),
                "ingested_at_utc": now,
            })
        if not rows:
            return 0
        with self._engine.begin() as conn:
            for iid, d, src in pks:
                conn.execute(
                    sa.delete(fund_nav_table)
                    .where(fund_nav_table.c.instrument_id == iid)
                    .where(fund_nav_table.c.market_local_date == d)
                    .where(fund_nav_table.c.source == src)
                )
            conn.execute(sa.insert(fund_nav_table), rows)
        return len(rows)

    def find_range(
        self,
        instrument_id: InstrumentId,
        start: date,
        end: date,
        *,
        as_of_utc: datetime | None = None,
    ) -> Sequence[FundNAV]:
        conds = [
            fund_nav_table.c.instrument_id == instrument_id.canonical(),
            fund_nav_table.c.market_local_date >= start,
            fund_nav_table.c.market_local_date <= end,
        ]
        if as_of_utc is not None:
            conds.append(fund_nav_table.c.available_at_utc <= as_of_utc)
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(fund_nav_table)
                .where(sa.and_(*conds))
                .order_by(fund_nav_table.c.market_local_date.asc())
            ).mappings().all()
        return [_row_to_nav(r) for r in rows]


def _row_to_nav(row) -> FundNAV:
    iid = parse_instrument_id(row["instrument_id"])
    et = row["event_time_utc"]
    avail = row["available_at_utc"]
    if et is not None and et.tzinfo is None:
        et = et.replace(tzinfo=timezone.utc)
    if avail is not None and avail.tzinfo is None:
        avail = avail.replace(tzinfo=timezone.utc)
    return FundNAV(
        instrument_id=iid,
        market_local_date=row["market_local_date"],
        event_time_utc=et,
        unit_nav=Decimal(str(row["unit_nav"])),
        accum_nav=Decimal(str(row["accum_nav"])) if row["accum_nav"] is not None else None,
        available_at_utc=avail,
        source=row["source"],
        source_version=row.get("source_version") or "unspecified",
        license_tag=row.get("license_tag") or "INTERNAL_RESEARCH",
        quality_status=row.get("quality_status") or "NORMAL",
    )


# ---------- fx_rate ---------- (migration 0006)
fx_rate_table = sa.Table(
    "fx_rate", metadata,
    sa.Column("base_ccy", sa.Text, nullable=False),
    sa.Column("quote_ccy", sa.Text, nullable=False),
    sa.Column("market_local_date", sa.Date, nullable=False),
    sa.Column("rate", sa.Numeric(20, 10), nullable=False),
    sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.PrimaryKeyConstraint("base_ccy", "quote_ccy", "market_local_date", "source"),
    sa.CheckConstraint("base_ccy <> quote_ccy", name="ck_fx_pair_distinct"),
)


class SqlFxRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, *, base: str, quote: str, d: date, rate: Decimal,
               available_at_utc: datetime, source: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(fx_rate_table)
                .where(fx_rate_table.c.base_ccy == base)
                .where(fx_rate_table.c.quote_ccy == quote)
                .where(fx_rate_table.c.market_local_date == d)
                .where(fx_rate_table.c.source == source)
            )
            conn.execute(sa.insert(fx_rate_table).values(
                base_ccy=base, quote_ccy=quote, market_local_date=d,
                rate=rate, available_at_utc=available_at_utc, source=source,
            ))

    def get_as_of(
        self, *, base: str, quote: str, on_or_before: date,
        as_of_utc: datetime | None = None,
    ) -> Decimal | None:
        conds = [
            fx_rate_table.c.base_ccy == base,
            fx_rate_table.c.quote_ccy == quote,
            fx_rate_table.c.market_local_date <= on_or_before,
        ]
        if as_of_utc is not None:
            conds.append(fx_rate_table.c.available_at_utc <= as_of_utc)
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(fx_rate_table.c.rate)
                .where(sa.and_(*conds))
                .order_by(fx_rate_table.c.market_local_date.desc())
                .limit(1)
            ).first()
        return Decimal(str(row[0])) if row else None


# ---------- portfolio_position ---------- (migration 0006)
portfolio_position_table = sa.Table(
    "portfolio_position", metadata,
    sa.Column("portfolio_id", sa.Text, nullable=False),
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("as_of_local_date", sa.Date, nullable=False),
    sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
    sa.Column("avg_cost_local", sa.Numeric(20, 6)),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("recorded_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("portfolio_id", "instrument_id", "as_of_local_date"),
)


class SqlPortfolioPositionRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(
        self, *, portfolio_id: str, instrument_id: InstrumentId,
        as_of_local_date: date, quantity: Decimal, currency: str,
        avg_cost_local: Decimal | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(portfolio_position_table)
                .where(portfolio_position_table.c.portfolio_id == portfolio_id)
                .where(portfolio_position_table.c.instrument_id == instrument_id.canonical())
                .where(portfolio_position_table.c.as_of_local_date == as_of_local_date)
            )
            conn.execute(sa.insert(portfolio_position_table).values(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id.canonical(),
                as_of_local_date=as_of_local_date,
                quantity=quantity,
                avg_cost_local=avg_cost_local,
                currency=currency,
                recorded_at_utc=datetime.now(timezone.utc),
            ))

    def snapshot(self, portfolio_id: str, *, as_of: date) -> list[dict]:
        """Return the latest position per instrument at or before ``as_of``."""
        t = portfolio_position_table
        subq = (
            sa.select(t.c.instrument_id,
                      sa.func.max(t.c.as_of_local_date).label("latest"))
            .where(t.c.portfolio_id == portfolio_id)
            .where(t.c.as_of_local_date <= as_of)
            .group_by(t.c.instrument_id)
            .subquery()
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(t)
                .join(subq, sa.and_(
                    t.c.instrument_id == subq.c.instrument_id,
                    t.c.as_of_local_date == subq.c.latest,
                ))
                .where(t.c.portfolio_id == portfolio_id)
            ).mappings().all()
        out: list[dict] = []
        for r in rows:
            out.append({
                "instrument_id": r["instrument_id"],
                "as_of_local_date": r["as_of_local_date"],
                "quantity": Decimal(str(r["quantity"])),
                "avg_cost_local": Decimal(str(r["avg_cost_local"])) if r["avg_cost_local"] is not None else None,
                "currency": r["currency"],
            })
        return out


__all__ = [
    "metadata",
    "corp_action_table",
    "fundamental_fact_table",
    "fund_nav_table",
    "fx_rate_table",
    "portfolio_position_table",
    "SqlCorporateActionRepository",
    "SqlFundamentalFactRepository",
    "SqlFundNavRepository",
    "SqlFxRepository",
    "SqlPortfolioPositionRepository",
]
