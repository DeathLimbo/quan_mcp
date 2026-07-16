"""0010 admin_job table — durable job state for Admin MCP (issue #3).

AdminTools kept jobs in an in-memory dict, lost on restart. This table backs
SqlJobStore so job status survives process restarts.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_admin_job"
down_revision = "0009_closed_loop_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_job",
        sa.Column("job_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False,
                  server_default=sa.text("'QUEUED'")),
        sa.Column("payload_json", sa.Text, nullable=True),
        sa.Column("result_json", sa.Text, nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_admin_job_status", "admin_job", ["status"])


def downgrade() -> None:
    op.drop_index("ix_admin_job_status", table_name="admin_job")
    op.drop_table("admin_job")
