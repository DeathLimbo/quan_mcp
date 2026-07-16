"""0013 factor governance: immutable FactorVersion with PIT availability (issue #10 Phase 4).

Adds the factor_version table. A factor version is immutable except its
``state`` (ACTIVE/RETIRED), which advances via the governance service.
``available_from`` is the point-in-time date from which the factor's data
exists — using a factor at an earlier as_of is a future-function leak and is
rejected by policy.validate_factor_availability.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_factor_governance"
down_revision = "0012_strategy_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_version",
        sa.Column("factor_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("available_from", sa.Date, nullable=False),
        sa.Column("dependencies_json", sa.Text, nullable=False,
                  server_default="[]"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("state", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("factor_id", "version"),
    )
    op.create_index("ix_factor_version_state", "factor_version", ["state"])
    op.create_index("ix_factor_version_available_from",
                    "factor_version", ["available_from"])


def downgrade() -> None:
    op.drop_index("ix_factor_version_available_from", table_name="factor_version")
    op.drop_index("ix_factor_version_state", table_name="factor_version")
    op.drop_table("factor_version")
