"""0012 strategy governance: immutable versioned strategy lifecycle (issue #10 Phase 2).

Adds the strategy-governance domain tables. Entities are immutable append-only
except ``strategy_version.state`` which advances through the lifecycle via the
single governance entry point (policy.validate_transition). Every state change
is mirrored as a ``promotion_decision`` row for audit.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_strategy_governance"
down_revision = "0011_model_registry_artifact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- parameter_set_version: immutable, content-hashed ----------------
    op.create_table(
        "parameter_set_version",
        sa.Column("content_hash", sa.String(64), primary_key=True),
        sa.Column("values_json", sa.Text, nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # ---- strategy_version: immutable except state -------------------------
    op.create_table(
        "strategy_version",
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("parent_version", sa.String(32), nullable=True),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("horizon_days", sa.Integer, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("parameter_set_hash", sa.String(64), nullable=False),
        sa.Column("feature_set_hash", sa.String(64), nullable=False),
        sa.Column("factor_refs_json", sa.Text, nullable=False,
                  server_default="[]"),
        sa.Column("model_ref", sa.String(128), nullable=True),
        sa.Column("code_commit", sa.String(64), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("approval_id", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("strategy_id", "version"),
        sa.ForeignKeyConstraint(["parameter_set_hash"],
                                ["parameter_set_version.content_hash"]),
    )
    op.create_index("ix_strategy_version_state",
                    "strategy_version", ["state"])
    op.create_index("ix_strategy_version_market_horizon",
                    "strategy_version", ["market", "horizon_days"])

    # ---- change_request: filed proposals (LLM/human) ---------------------
    op.create_table(
        "change_request",
        sa.Column("request_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("parent_version", sa.String(32), nullable=True),
        sa.Column("proposed_parameters_json", sa.Text, nullable=False),
        sa.Column("proposed_factor_refs_json", sa.Text, nullable=False,
                  server_default="[]"),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("derived_version", sa.String(32), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
    )
    op.create_index("ix_change_request_strategy",
                    "change_request", ["strategy_id", "status"])

    # ---- evaluation_run: deterministic evaluation records -----------------
    op.create_table(
        "evaluation_run",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("window_start", sa.String(32), nullable=False),
        sa.Column("window_end", sa.String(32), nullable=False),
        sa.Column("regime_slices_json", sa.Text, nullable=False,
                  server_default="[]"),
        sa.Column("metrics_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("started_by", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repro_hash", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
    )
    op.create_index("ix_evaluation_run_strategy_version",
                    "evaluation_run", ["strategy_id", "version", "status"])

    # ---- promotion_decision: append-only audit of every state change -----
    op.create_table(
        "promotion_decision",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=False),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("evaluation_run_id", sa.String(64), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column("approval_id", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("reason", sa.Text, nullable=True),
    )
    op.create_index("ix_promotion_decision_strategy_version",
                    "promotion_decision", ["strategy_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_promotion_decision_strategy_version",
                  table_name="promotion_decision")
    op.drop_table("promotion_decision")
    op.drop_index("ix_evaluation_run_strategy_version",
                  table_name="evaluation_run")
    op.drop_table("evaluation_run")
    op.drop_index("ix_change_request_strategy",
                  table_name="change_request")
    op.drop_table("change_request")
    op.drop_index("ix_strategy_version_market_horizon",
                  table_name="strategy_version")
    op.drop_index("ix_strategy_version_state", table_name="strategy_version")
    op.drop_table("strategy_version")
    op.drop_table("parameter_set_version")
