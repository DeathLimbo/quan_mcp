"""0009 closed-loop persistence: model registry, predictions, dataset
snapshots, paper ledger, decision review (issue #2).

The V4 closed loop requires durable tables for model governance, prediction
auditability, dataset/feature reproducibility, and paper-trading
reconciliation. Existing migrations covered market-data foundations; this
migration adds the operational closed-loop tables so a restart no longer
erases model state, predictions, paper fills, or user confirmations.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_closed_loop_persistence"
down_revision = "0008_ca_nav_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- model_registry: model governance state (survives restart) --------
    op.create_table(
        "model_registry",
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("market", sa.Text, nullable=False),
        sa.Column("horizon_days", sa.Integer, nullable=False),
        sa.Column("feature_set_hash", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False,
                  server_default=sa.text("'DRAFT'")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.Text, nullable=True),
        sa.Column("approval_id", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("model_id", "version"),
    )
    op.create_index("ix_model_registry_state", "model_registry", ["state"])

    # ---- model_prediction: every forecast is traceable + auditable --------
    op.create_table(
        "model_prediction",
        sa.Column("prediction_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, nullable=False),
        sa.Column("market", sa.Text, nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_days", sa.Integer, nullable=False),
        sa.Column("model_id", sa.Text, nullable=True),
        sa.Column("model_version", sa.Text, nullable=True),
        sa.Column("feature_hash", sa.Text, nullable=True),
        # Provenance versions (spec §38 数据 — trace to data/calendar/rule/source)
        sa.Column("data_version", sa.Text, nullable=True),
        sa.Column("calendar_version", sa.Text, nullable=True),
        sa.Column("rule_version", sa.Text, nullable=True),
        sa.Column("source_version", sa.Text, nullable=True),
        sa.Column("score", sa.Numeric(20, 10), nullable=True),  # None if NoForecast
        sa.Column("no_forecast_reason", sa.Text, nullable=True),
        sa.Column("trace_id", sa.Text, nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("prediction_id"),
    )
    op.create_index("ix_model_prediction_model", "model_prediction",
                    ["model_id", "model_version"])
    op.create_index("ix_model_prediction_as_of", "model_prediction", ["as_of_utc"])

    # ---- dataset_snapshot: dataset/feature reproducibility ----------------
    op.create_table(
        "dataset_snapshot",
        sa.Column("snapshot_id", sa.Text, nullable=False),
        sa.Column("dataset_name", sa.Text, nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_hash", sa.Text, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )

    # ---- paper_order: order intent linked to forecast + risk decision -----
    op.create_table(
        "paper_order",
        sa.Column("order_id", sa.Text, nullable=False),
        sa.Column("portfolio_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, nullable=False),
        sa.Column("side", sa.Integer, nullable=False),  # +1 buy, -1 sell
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("ref_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("intent", sa.Text, nullable=False),  # market / limit
        sa.Column("forecast_id", sa.Text, nullable=True),  # reconcile to prediction
        sa.Column("risk_trace_id", sa.Text, nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )

    # ---- paper_fill: executed fills reconcilable to forecast/risk ---------
    op.create_table(
        "paper_fill",
        sa.Column("fill_id", sa.Text, nullable=False),
        sa.Column("order_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.Text, nullable=False),
        sa.Column("side", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("fill_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("fill_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commission", sa.Numeric(20, 6), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("forecast_id", sa.Text, nullable=True),  # reconcile link
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("fill_id"),
    )
    op.create_index("ix_paper_fill_order", "paper_fill", ["order_id"])

    # ---- decision_review: forecast explanation / user confirmation --------
    op.create_table(
        "decision_review",
        sa.Column("review_id", sa.Text, nullable=False),
        sa.Column("prediction_id", sa.Text, nullable=False),
        sa.Column("explanation", sa.Text, nullable=True),
        sa.Column("confirmed", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("confirmed_by", sa.Text, nullable=True),
        sa.Column("confirmed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("review_id"),
    )


def downgrade() -> None:
    op.drop_table("decision_review")
    op.drop_table("paper_fill")
    op.drop_index("ix_paper_fill_order", table_name="paper_fill")
    op.drop_table("paper_order")
    op.drop_table("dataset_snapshot")
    op.drop_index("ix_model_prediction_as_of", table_name="model_prediction")
    op.drop_index("ix_model_prediction_model", table_name="model_prediction")
    op.drop_table("model_prediction")
    op.drop_index("ix_model_registry_state", table_name="model_registry")
    op.drop_table("model_registry")
