"""Durable persistence layer (issues #2, #3).

Repositories for the closed-loop tables added in migration 0009:
- SqlPredictionRepository: every Forecast/NoForecast persisted with full
  provenance (instrument/as_of/model id+ver/feature hash/data+calendar+rule+
  source versions/score/reason/trace id) so predictions are auditable and
  traceable (spec §38 数据 + issue #2).
- SqlPaperLedger: order intents + fills reconcilable to the originating
  forecast/risk decision (issue #2 paper-trading reconciliation).
- SqlJobStore: durable admin job state surviving restart (issue #3).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from packages.common.instrument_id import Market
from packages.inference.service import Forecast, NoForecast
from packages.models.registry import ModelRecord, ModelState

_metadata = sa.MetaData()

model_registry_t = sa.Table(
    "model_registry", _metadata,
    sa.Column("model_id", sa.Text, primary_key=True),
    sa.Column("version", sa.Text, primary_key=True),
    sa.Column("market", sa.Text, nullable=False),
    sa.Column("horizon_days", sa.Integer, nullable=False),
    sa.Column("feature_set_hash", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("approved_by", sa.Text),
    sa.Column("approval_id", sa.Text),
    sa.Column("notes", sa.Text),
    sa.Column("artifact_path", sa.Text),
    sa.Column("task", sa.Text),
    sa.Column("feature_names_json", sa.Text),
    sa.Column("metrics_json", sa.Text),
)

model_prediction_t = sa.Table(
    "model_prediction", _metadata,
    sa.Column("prediction_id", sa.Text, primary_key=True),
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("market", sa.Text, nullable=False),
    sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("horizon_days", sa.Integer, nullable=False),
    sa.Column("model_id", sa.Text),
    sa.Column("model_version", sa.Text),
    sa.Column("feature_hash", sa.Text),
    sa.Column("data_version", sa.Text),
    sa.Column("calendar_version", sa.Text),
    sa.Column("rule_version", sa.Text),
    sa.Column("source_version", sa.Text),
    sa.Column("score", sa.Numeric(20, 10)),
    sa.Column("no_forecast_reason", sa.Text),
    sa.Column("trace_id", sa.Text),
    sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
)

paper_order_t = sa.Table(
    "paper_order", _metadata,
    sa.Column("order_id", sa.Text, primary_key=True),
    sa.Column("portfolio_id", sa.Text, nullable=False),
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("side", sa.Integer, nullable=False),
    sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
    sa.Column("ref_price", sa.Numeric(20, 6), nullable=False),
    sa.Column("intent", sa.Text, nullable=False),
    sa.Column("forecast_id", sa.Text),
    sa.Column("risk_trace_id", sa.Text),
    sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
)

paper_fill_t = sa.Table(
    "paper_fill", _metadata,
    sa.Column("fill_id", sa.Text, primary_key=True),
    sa.Column("order_id", sa.Text, nullable=False),
    sa.Column("instrument_id", sa.Text, nullable=False),
    sa.Column("side", sa.Integer, nullable=False),
    sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
    sa.Column("fill_price", sa.Numeric(20, 6), nullable=False),
    sa.Column("fill_time_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("commission", sa.Numeric(20, 6), nullable=False, default=0),
    sa.Column("forecast_id", sa.Text),
    sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
)

admin_job_t = sa.Table(
    "admin_job", _metadata,
    sa.Column("job_id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),  # QUEUED/RUNNING/SUCCESS/FAILED
    sa.Column("payload_json", sa.Text),
    sa.Column("result_json", sa.Text),
    sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
)


class SqlPredictionRepository:
    """Persist every prediction with full provenance (issue #2)."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def record(self, result: Forecast | NoForecast, *,
               trace_id: str | None = None) -> str:
        pid = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        if isinstance(result, Forecast):
            row: dict[str, Any] = {
                "prediction_id": pid,
                "instrument_id": result.instrument_id.canonical(),
                "market": result.instrument_id.market.value,
                "as_of_utc": result.as_of,
                "horizon_days": result.horizon_days,
                "model_id": result.model_id,
                "model_version": result.model_version,
                "feature_hash": result.feature_hash,
                # data_version is stamped from bars[-1].source_version (issue
                # #1 留痕); source_version mirrors it for explicit traceability.
                "data_version": result.data_version,
                "calendar_version": result.calendar_version,
                "rule_version": result.rule_version,
                "source_version": result.data_version,
                "score": Decimal(str(result.score)),
                "no_forecast_reason": None,
                "trace_id": trace_id,
                "created_at_utc": now,
            }
        else:
            row = {
                "prediction_id": pid,
                "instrument_id": result.instrument_id.canonical(),
                "market": result.instrument_id.market.value,
                "as_of_utc": result.as_of,
                "horizon_days": 0,
                "model_id": None, "model_version": None, "feature_hash": None,
                "data_version": None, "calendar_version": None,
                "rule_version": None, "source_version": None,
                "score": None,
                "no_forecast_reason": result.reason.value,
                "trace_id": trace_id,
                "created_at_utc": now,
            }
        with self._engine.begin() as conn:
            conn.execute(sa.insert(model_prediction_t).values(**row))
        return pid

    def get(self, prediction_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(model_prediction_t).where(
                    model_prediction_t.c.prediction_id == prediction_id)
            ).first()
        return dict(row._mapping) if row else None

    def list_by_model(self, model_id: str, version: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(model_prediction_t).where(
                    model_prediction_t.c.model_id == model_id,
                    model_prediction_t.c.model_version == version,
                )
            ).all()
        return [dict(r._mapping) for r in rows]


class SqlPaperLedger:
    """Paper order/fill persistence reconcilable to forecasts (issue #2)."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def record_order(self, *, portfolio_id: str, instrument_id: str,
                     side: int, quantity: float, ref_price: float,
                     intent: str = "market", forecast_id: str | None = None,
                     risk_trace_id: str | None = None) -> str:
        oid = uuid.uuid4().hex
        with self._engine.begin() as conn:
            conn.execute(sa.insert(paper_order_t).values(
                order_id=oid, portfolio_id=portfolio_id,
                instrument_id=instrument_id, side=side,
                quantity=Decimal(str(quantity)),
                ref_price=Decimal(str(ref_price)),
                intent=intent, forecast_id=forecast_id,
                risk_trace_id=risk_trace_id,
                created_at_utc=datetime.now(timezone.utc),
            ))
        return oid

    def record_fill(self, *, order_id: str, instrument_id: str, side: int,
                    quantity: float, fill_price: float, fill_time_utc: datetime,
                    commission: float = 0.0,
                    forecast_id: str | None = None) -> str:
        fid = uuid.uuid4().hex
        with self._engine.begin() as conn:
            conn.execute(sa.insert(paper_fill_t).values(
                fill_id=fid, order_id=order_id, instrument_id=instrument_id,
                side=side, quantity=Decimal(str(quantity)),
                fill_price=Decimal(str(fill_price)),
                fill_time_utc=fill_time_utc,
                commission=Decimal(str(commission)),
                forecast_id=forecast_id,
                created_at_utc=datetime.now(timezone.utc),
            ))
        return fid

    def fills_by_forecast(self, forecast_id: str) -> list[dict[str, Any]]:
        """Reconcile fills to their originating forecast (issue #2)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(paper_fill_t).where(
                    paper_fill_t.c.forecast_id == forecast_id)
            ).all()
        return [dict(r._mapping) for r in rows]


class SqlJobStore:
    """Durable admin job state surviving restart (issue #3)."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def create(self, *, kind: str, payload_json: str | None = None,
               job_id: str | None = None) -> str:
        jid = job_id or uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(admin_job_t).values(
                job_id=jid, kind=kind, status="QUEUED",
                payload_json=payload_json, result_json=None,
                created_at_utc=now, updated_at_utc=now,
            ))
        return jid

    def update(self, job_id: str, *, status: str,
               result_json: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(admin_job_t).where(
                    admin_job_t.c.job_id == job_id
                ).values(status=status, result_json=result_json, updated_at_utc=now)
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(admin_job_t).where(admin_job_t.c.job_id == job_id)
            ).first()
        return dict(row._mapping) if row else None


class SqlModelRegistry:
    """Durable model registry — metadata in the model_registry table, LightGBM
    booster artifacts on the filesystem. Survives restart so PRODUCTION models
    reload in milliseconds instead of being retrained every run.

    Implements the ModelRegistry Protocol (register/transition/get_production)
    plus artifact save/load. ``register`` serializes the artifact via its
    ``save()`` method; ``get_artifact`` reloads it via ``TrainedLightGBMModel.load``.
    """

    def __init__(self, engine: sa.Engine, model_store_dir: str) -> None:
        self._engine = engine
        self._store = model_store_dir

    def register(self, rec: ModelRecord, *, artifact: object | None = None,
                 metrics: dict[str, float] | None = None) -> None:
        artifact_path = None
        task = None
        feat_names_json = None
        if artifact is not None and hasattr(artifact, "save"):
            p = f"{self._store}/{rec.model_id}_{rec.version}"
            artifact.save(p)
            artifact_path = p
            task = getattr(artifact, "task", None)
            feat_names = list(getattr(artifact, "feature_names", ()))
            feat_names_json = json.dumps(feat_names)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(model_registry_t).values(
                model_id=rec.model_id, version=rec.version,
                market=rec.market.value, horizon_days=rec.horizon_days,
                feature_set_hash=rec.feature_set_hash,
                state=rec.state.value,
                created_at_utc=rec.created_at,
                approved_by=rec.approved_by, approval_id=rec.approval_id,
                notes=rec.notes, artifact_path=artifact_path, task=task,
                feature_names_json=feat_names_json,
                metrics_json=json.dumps(metrics) if metrics else None,
            ))

    def transition(self, model_id: str, version: str, to: ModelState, *,
                   actor: str, approval_id: str | None = None,
                   metrics: dict[str, float] | None = None) -> ModelRecord:
        with self._engine.begin() as conn:
            row = conn.execute(sa.select(model_registry_t).where(
                model_registry_t.c.model_id == model_id,
                model_registry_t.c.version == version,
            )).first()
            if row is None:
                raise KeyError(f"unknown model {model_id}@{version}")
            vals: dict[str, Any] = {"state": to.value}
            if to is ModelState.PRODUCTION:
                vals["approved_by"] = actor
                vals["approval_id"] = approval_id
            if metrics:
                vals["metrics_json"] = json.dumps(metrics)
            conn.execute(sa.update(model_registry_t).where(
                model_registry_t.c.model_id == model_id,
                model_registry_t.c.version == version,
            ).values(**vals))
        return self.get(model_id, version)

    def get(self, model_id: str, version: str) -> ModelRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(model_registry_t).where(
                model_registry_t.c.model_id == model_id,
                model_registry_t.c.version == version,
            )).first()
        return self._row_to_record(row) if row else None

    def get_production(self, market: Market, horizon_days: int) -> ModelRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(model_registry_t).where(
                model_registry_t.c.state == "PRODUCTION",
                model_registry_t.c.market == market.value,
                model_registry_t.c.horizon_days == horizon_days,
            ).order_by(model_registry_t.c.created_at_utc.desc()).limit(1)).first()
        return self._row_to_record(row) if row else None

    def get_artifact(self, model_id: str, version: str) -> object | None:
        """Reload the serialized LightGBM booster (None if no artifact stored)."""
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(model_registry_t.c.artifact_path).where(
                model_registry_t.c.model_id == model_id,
                model_registry_t.c.version == version,
            )).first()
        if not row or not row[0]:
            return None
        from packages.training.lightgbm_trainer import TrainedLightGBMModel
        return TrainedLightGBMModel.load(row[0])

    def get_production_artifact(self, market: Market,
                                horizon_days: int) -> tuple[ModelRecord | None, object | None]:
        """PRODUCTION record + reloaded artifact (None, None if absent)."""
        rec = self.get_production(market, horizon_days)
        if rec is None:
            return None, None
        return rec, self.get_artifact(rec.model_id, rec.version)

    def get_latest_production(self, model_id: str) -> tuple[ModelRecord | None, object | None]:
        """Latest PRODUCTION version of a given model_id.

        Allows multiple PRODUCTION models per (market, horizon) as long as they
        differ by model_id — e.g. CN A-share equity and CN fund-NAV models both
        live under market=CN, horizon=20 but distinct model_ids.
        """
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(model_registry_t).where(
                model_registry_t.c.model_id == model_id,
                model_registry_t.c.state == "PRODUCTION",
            ).order_by(model_registry_t.c.created_at_utc.desc()).limit(1)).first()
        if not row:
            return None, None
        rec = self._row_to_record(row)
        return rec, self.get_artifact(rec.model_id, rec.version)

    @staticmethod
    def _row_to_record(row) -> ModelRecord:
        metrics: dict[str, float] = {}
        if row.metrics_json:
            try:
                metrics = json.loads(row.metrics_json)
            except Exception:
                metrics = {}
        return ModelRecord(
            model_id=row.model_id, version=row.version,
            market=Market(row.market), horizon_days=row.horizon_days,
            feature_set_hash=row.feature_set_hash,
            state=ModelState(row.state), created_at=row.created_at_utc,
            approved_by=row.approved_by, approval_id=row.approval_id,
            metrics=metrics, notes=row.notes,
        )
