"""0006 fund_nav_daily + fx_rate + portfolio_position.

Grouped because each is small and they share the same PIT invariant pattern.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_fund_fx_portfolio"
down_revision = "0005_fundamental_fact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fund_nav_daily",
        sa.Column("instrument_id", sa.Text, sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("market_local_date", sa.Date, nullable=False),
        sa.Column("event_time_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("unit_nav",  sa.Numeric(20, 6), nullable=False),
        sa.Column("accum_nav", sa.Numeric(20, 6)),
        sa.Column("available_at_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("ingested_at_utc", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("instrument_id", "market_local_date", "source"),
        sa.CheckConstraint("available_at_utc >= event_time_utc", name="ck_fund_nav_pit"),
    )
    op.create_index("ix_fund_nav_date", "fund_nav_daily", ["market_local_date"])

    op.create_table(
        "fx_rate",
        sa.Column("base_ccy",  sa.Text, nullable=False),
        sa.Column("quote_ccy", sa.Text, nullable=False),
        sa.Column("market_local_date", sa.Date, nullable=False),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("available_at_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("base_ccy", "quote_ccy", "market_local_date", "source"),
        sa.CheckConstraint("base_ccy <> quote_ccy", name="ck_fx_pair_distinct"),
    )
    op.create_index("ix_fx_rate_avail", "fx_rate", ["available_at_utc"])

    op.create_table(
        "portfolio_position",
        sa.Column("portfolio_id",  sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("as_of_local_date", sa.Date, nullable=False),
        sa.Column("quantity",        sa.Numeric(24, 8), nullable=False),
        sa.Column("avg_cost_local",  sa.Numeric(20, 6)),
        sa.Column("currency",        sa.Text, nullable=False),
        sa.Column("recorded_at_utc", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("portfolio_id", "instrument_id", "as_of_local_date"),
    )
    op.create_index("ix_pos_portfolio", "portfolio_position", ["portfolio_id"])


def downgrade() -> None:
    op.drop_index("ix_pos_portfolio", table_name="portfolio_position")
    op.drop_table("portfolio_position")
    op.drop_index("ix_fx_rate_avail", table_name="fx_rate")
    op.drop_table("fx_rate")
    op.drop_index("ix_fund_nav_date", table_name="fund_nav_daily")
    op.drop_table("fund_nav_daily")
