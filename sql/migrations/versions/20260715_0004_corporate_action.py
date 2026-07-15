"""0004 corporate_action — splits / dividends / mergers / spinoffs / rights.

Adjustment consumers read this table by (instrument_id, ex_date_local).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_corporate_action"
down_revision = "0003_market_bar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corporate_action",
        sa.Column("action_id", sa.Text, primary_key=True),
        sa.Column("instrument_id", sa.Text, sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),   # SPLIT|DIVIDEND|MERGER|SPINOFF|RIGHTS
        sa.Column("announcement_date_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ex_date_local", sa.Date, nullable=False),
        sa.Column("payable_date_local", sa.Date),
        sa.Column("ratio", sa.Numeric(24, 10)),
        sa.Column("currency", sa.Text),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("available_at_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ingested_at_utc", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "action_type IN ('SPLIT','DIVIDEND','MERGER','SPINOFF','RIGHTS')",
            name="ck_ca_type",
        ),
    )
    op.create_index("ix_ca_instrument_ex", "corporate_action", ["instrument_id", "ex_date_local"])


def downgrade() -> None:
    op.drop_index("ix_ca_instrument_ex", table_name="corporate_action")
    op.drop_table("corporate_action")
