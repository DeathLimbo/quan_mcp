"""0007 market_bar provenance — source_version / license_tag / quality_status.

Backfills the three §7.4 unified-adapter fields on the ``market_bar`` table
with conservative server defaults so pre-existing rows remain valid. New
rows written by adapters that stamp the fields (AKShare / yfinance / fake)
carry the real provenance value.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_market_bar_provenance"
down_revision = "0006_fund_fx_portfolio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_bar",
        sa.Column("source_version", sa.Text, nullable=False,
                  server_default=sa.text("'unspecified'")),
    )
    op.add_column(
        "market_bar",
        sa.Column("license_tag", sa.Text, nullable=False,
                  server_default=sa.text("'INTERNAL_RESEARCH'")),
    )
    op.add_column(
        "market_bar",
        sa.Column("quality_status", sa.Text, nullable=False,
                  server_default=sa.text("'NORMAL'")),
    )


def downgrade() -> None:
    op.drop_column("market_bar", "quality_status")
    op.drop_column("market_bar", "license_tag")
    op.drop_column("market_bar", "source_version")
