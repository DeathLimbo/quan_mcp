"""0005 fundamental_fact — point-in-time fundamentals & fund attributes.

``available_at_utc`` must be >= ``as_of_utc`` (fact valid-from date). Queries
"as of date X" use ``bisect_right(available_at_utc, X) - 1``.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_fundamental_fact"
down_revision = "0004_corporate_action"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fundamental_fact",
        sa.Column("instrument_id", sa.Text, sa.ForeignKey("instruments.instrument_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fact_name", sa.Text, nullable=False),
        sa.Column("as_of_utc", sa.TIMESTAMP(timezone=True), nullable=False),        # fiscal / event date
        sa.Column("available_at_utc", sa.TIMESTAMP(timezone=True), nullable=False), # when we can consume
        sa.Column("value_num", sa.Numeric(30, 10)),
        sa.Column("value_text", sa.Text),
        sa.Column("unit", sa.Text),
        sa.Column("period_end_local", sa.Date),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("ingested_at_utc", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("instrument_id", "fact_name", "as_of_utc", "source"),
        sa.CheckConstraint("available_at_utc >= as_of_utc", name="ck_fund_fact_pit"),
    )
    op.create_index("ix_fund_fact_available", "fundamental_fact", ["available_at_utc"])
    op.create_index("ix_fund_fact_instr_name", "fundamental_fact", ["instrument_id", "fact_name"])


def downgrade() -> None:
    op.drop_index("ix_fund_fact_instr_name", table_name="fundamental_fact")
    op.drop_index("ix_fund_fact_available", table_name="fundamental_fact")
    op.drop_table("fundamental_fact")
