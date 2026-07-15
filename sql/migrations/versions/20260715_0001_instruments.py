"""0001 instruments core table

Canonical InstrumentId storage. `ticker` is descriptive, not unique.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_instruments"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("instrument_id", sa.Text, primary_key=True),  # canonical form
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
        sa.Column("status", sa.Text, nullable=False, server_default="ACTIVE"),
        sa.Column("ingested_at_utc", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("calendar_version", sa.Text, nullable=False, server_default="v0"),
        sa.Column("rule_version", sa.Text, nullable=False, server_default="v0"),
    )
    op.create_index("ix_instruments_market_asset", "instruments", ["market", "asset_type"])
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"])

    op.create_table(
        "instrument_aliases",
        sa.Column("alias", sa.Text, nullable=False),
        sa.Column("alias_source", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, sa.ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False),
        sa.Column("valid_from_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("valid_to_utc", sa.TIMESTAMP(timezone=True)),
        sa.PrimaryKeyConstraint("alias", "alias_source", "valid_from_utc"),
    )
    op.create_index("ix_instrument_aliases_id", "instrument_aliases", ["instrument_id"])


def downgrade() -> None:
    op.drop_index("ix_instrument_aliases_id", table_name="instrument_aliases")
    op.drop_table("instrument_aliases")
    op.drop_index("ix_instruments_symbol", table_name="instruments")
    op.drop_index("ix_instruments_market_asset", table_name="instruments")
    op.drop_table("instruments")
