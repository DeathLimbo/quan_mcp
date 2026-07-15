"""0003 market_bar — daily OHLCV per instrument.

Point-in-time enforced via ``available_at_utc`` >= ``event_time_utc``.
Adjustments carried as multiplicative cumulative factor per row so
retro-adjustment does not rewrite history.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_market_bar"
down_revision = "0002_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_bar",
        sa.Column("instrument_id", sa.Text, sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("market_local_date", sa.Date, nullable=False),
        sa.Column("event_time_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open",    sa.Numeric(20, 6), nullable=False),
        sa.Column("high",    sa.Numeric(20, 6), nullable=False),
        sa.Column("low",     sa.Numeric(20, 6), nullable=False),
        sa.Column("close",   sa.Numeric(20, 6), nullable=False),
        sa.Column("volume",  sa.Numeric(24, 4), nullable=False, server_default="0"),
        sa.Column("turnover", sa.Numeric(24, 4)),
        sa.Column("adj_factor", sa.Numeric(20, 10)),
        sa.Column("available_at_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source",  sa.Text, nullable=False),
        sa.Column("calendar_version", sa.Text, nullable=False),
        sa.Column("rule_version",     sa.Text, nullable=False),
        sa.Column("ingested_at_utc", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("instrument_id", "market_local_date", "source"),
        sa.CheckConstraint("available_at_utc >= event_time_utc", name="ck_market_bar_pit"),
        sa.CheckConstraint("high >= low", name="ck_market_bar_range"),
    )
    op.create_index("ix_market_bar_date", "market_bar", ["market_local_date"])
    op.create_index("ix_market_bar_available", "market_bar", ["available_at_utc"])


def downgrade() -> None:
    op.drop_index("ix_market_bar_available", table_name="market_bar")
    op.drop_index("ix_market_bar_date", table_name="market_bar")
    op.drop_table("market_bar")
