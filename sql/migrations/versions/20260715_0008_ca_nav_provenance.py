"""0008 corp_action + fund_nav provenance columns.

Spec §7.4 requires all Adapter outputs — not just OHLCV bars — to carry
``source_version`` / ``license_tag`` / ``quality_status``. Migration 0007
handled the ``market_bar`` table; this migration backfills the same three
columns on ``corporate_action`` and ``fund_nav_daily`` with conservative
server defaults so pre-existing rows remain valid.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_ca_nav_provenance"
down_revision = "0007_market_bar_provenance"
branch_labels = None
depends_on = None


_TABLES = ("corporate_action", "fund_nav_daily")


def upgrade() -> None:
    for tbl in _TABLES:
        op.add_column(
            tbl,
            sa.Column("source_version", sa.Text, nullable=False,
                      server_default=sa.text("'unspecified'")),
        )
        op.add_column(
            tbl,
            sa.Column("license_tag", sa.Text, nullable=False,
                      server_default=sa.text("'INTERNAL_RESEARCH'")),
        )
        op.add_column(
            tbl,
            sa.Column("quality_status", sa.Text, nullable=False,
                      server_default=sa.text("'NORMAL'")),
        )


def downgrade() -> None:
    for tbl in _TABLES:
        op.drop_column(tbl, "quality_status")
        op.drop_column(tbl, "license_tag")
        op.drop_column(tbl, "source_version")
