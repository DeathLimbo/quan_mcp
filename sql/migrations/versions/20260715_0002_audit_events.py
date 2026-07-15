"""0002 audit_events

Append-only. INSERT-only role recommended at DB level.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_audit_events"
down_revision = "0001_instruments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.Text, nullable=False),
        sa.Column("actor_type", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("resource_type", sa.Text, nullable=False),
        sa.Column("resource_id", sa.Text, nullable=False),
        sa.Column("before_hash", sa.Text),
        sa.Column("after_hash", sa.Text),
        sa.Column("request_id", sa.Text),
        sa.Column("trace_id", sa.Text),
        sa.Column("ip_or_service_identity", sa.Text, nullable=False),
        sa.Column("approval_id", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_audit_actor", "audit_events", ["actor_id", "created_at"])
    op.create_index("ix_audit_resource", "audit_events", ["resource_type", "resource_id", "created_at"])
    op.create_index("ix_audit_trace", "audit_events", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_trace", table_name="audit_events")
    op.drop_index("ix_audit_resource", table_name="audit_events")
    op.drop_index("ix_audit_actor", table_name="audit_events")
    op.drop_table("audit_events")
