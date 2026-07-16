"""0011 model_registry: artifact storage + task/feature_names/metrics columns.

Enables durable model persistence — the registry now records where the
serialized LightGBM booster lives on disk, the task mode
(classification/regression), the feature-name list needed to reload the
artifact, and training metrics for governance review. Without these columns
the model is retrained on every run (~90s) and never accumulates learning.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_model_registry_artifact"
down_revision = "0010_admin_job"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_registry", sa.Column("artifact_path", sa.Text, nullable=True))
    op.add_column("model_registry", sa.Column("task", sa.Text, nullable=True))
    op.add_column("model_registry", sa.Column("feature_names_json", sa.Text, nullable=True))
    op.add_column("model_registry", sa.Column("metrics_json", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("model_registry", "metrics_json")
    op.drop_column("model_registry", "feature_names_json")
    op.drop_column("model_registry", "task")
    op.drop_column("model_registry", "artifact_path")
