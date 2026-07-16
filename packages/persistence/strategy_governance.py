"""SQL implementation of the strategy-governance repositories.

Lives in its own file (issue #10 review: do not bloat repositories.py). All
state mutations go through ``compare_and_set_state`` — no plain update method
exists, so concurrent promotions lose loudly.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from packages.common.instrument_id import Market
from packages.common.time_utils import utcnow
from packages.strategy_governance.domain import (
    ChangeRequest,
    ChangeRequestStatus,
    EvaluationRun,
    EvaluationStatus,
    ParameterSetVersion,
    PromotionDecision,
    PromotionOutcome,
    StrategyState,
    StrategyVersion,
)

_metadata = sa.MetaData()

parameter_set_version_t = sa.Table(
    "parameter_set_version", _metadata,
    sa.Column("content_hash", sa.String(64), primary_key=True),
    sa.Column("values_json", sa.Text, nullable=False),
    sa.Column("schema_version", sa.String(32), nullable=False),
    sa.Column("created_by", sa.String(128), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

strategy_version_t = sa.Table(
    "strategy_version", _metadata,
    sa.Column("strategy_id", sa.String(64), nullable=False),
    sa.Column("version", sa.String(32), nullable=False),
    sa.Column("parent_version", sa.String(32), nullable=True),
    sa.Column("market", sa.String(16), nullable=False),
    sa.Column("horizon_days", sa.Integer, nullable=False),
    sa.Column("state", sa.String(24), nullable=False),
    sa.Column("parameter_set_hash", sa.String(64), nullable=False),
    sa.Column("feature_set_hash", sa.String(64), nullable=False),
    sa.Column("factor_refs_json", sa.Text, nullable=False),
    sa.Column("model_ref", sa.String(128), nullable=True),
    sa.Column("code_commit", sa.String(64), nullable=True),
    sa.Column("config_hash", sa.String(64), nullable=True),
    sa.Column("created_by", sa.String(128), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("approved_by", sa.String(128), nullable=True),
    sa.Column("approval_id", sa.String(128), nullable=True),
    sa.PrimaryKeyConstraint("strategy_id", "version"),
)

change_request_t = sa.Table(
    "change_request", _metadata,
    sa.Column("request_id", sa.String(64), primary_key=True),
    sa.Column("strategy_id", sa.String(64), nullable=False),
    sa.Column("parent_version", sa.String(32), nullable=True),
    sa.Column("proposed_parameters_json", sa.Text, nullable=False),
    sa.Column("proposed_factor_refs_json", sa.Text, nullable=False),
    sa.Column("rationale", sa.Text, nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("created_by", sa.String(128), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("derived_version", sa.String(32), nullable=True),
    sa.Column("decided_by", sa.String(128), nullable=True),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("rejection_reason", sa.Text, nullable=True),
)

evaluation_run_t = sa.Table(
    "evaluation_run", _metadata,
    sa.Column("run_id", sa.String(64), primary_key=True),
    sa.Column("strategy_id", sa.String(64), nullable=False),
    sa.Column("version", sa.String(32), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("window_start", sa.String(32), nullable=False),
    sa.Column("window_end", sa.String(32), nullable=False),
    sa.Column("regime_slices_json", sa.Text, nullable=False),
    sa.Column("metrics_json", sa.Text, nullable=False),
    sa.Column("started_by", sa.String(128), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("repro_hash", sa.String(64), nullable=True),
    sa.Column("failure_reason", sa.Text, nullable=True),
)

promotion_decision_t = sa.Table(
    "promotion_decision", _metadata,
    sa.Column("decision_id", sa.String(64), primary_key=True),
    sa.Column("strategy_id", sa.String(64), nullable=False),
    sa.Column("version", sa.String(32), nullable=False),
    sa.Column("from_state", sa.String(24), nullable=False),
    sa.Column("to_state", sa.String(24), nullable=False),
    sa.Column("outcome", sa.String(24), nullable=False),
    sa.Column("evaluation_run_id", sa.String(64), nullable=True),
    sa.Column("decided_by", sa.String(128), nullable=False),
    sa.Column("approval_id", sa.String(128), nullable=True),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
)


# --------------------------------------------------------------------------- #
# Serialization helpers (row <-> domain entity)
# --------------------------------------------------------------------------- #
def _row_to_param_set(row: Any) -> ParameterSetVersion:
    m = row._mapping
    return ParameterSetVersion(
        values=json.loads(m["values_json"]),
        schema_version=m["schema_version"],
        content_hash=m["content_hash"],
        created_by=m["created_by"],
        created_at=m["created_at"],
    )


def _row_to_strategy_version(row: Any, param_set: ParameterSetVersion) -> StrategyVersion:
    m = row._mapping
    return StrategyVersion(
        strategy_id=m["strategy_id"],
        version=m["version"],
        parent_version=m["parent_version"],
        market=Market(m["market"]),
        horizon_days=m["horizon_days"],
        state=StrategyState(m["state"]),
        parameter_set=param_set,
        feature_set_hash=m["feature_set_hash"],
        factor_refs=tuple(json.loads(m["factor_refs_json"])),
        model_ref=m["model_ref"],
        code_commit=m["code_commit"],
        config_hash=m["config_hash"],
        created_by=m["created_by"],
        created_at=m["created_at"],
        approved_by=m["approved_by"],
        approval_id=m["approval_id"],
    )


def _row_to_change_request(row: Any) -> ChangeRequest:
    m = row._mapping
    return ChangeRequest(
        request_id=m["request_id"],
        strategy_id=m["strategy_id"],
        parent_version=m["parent_version"],
        proposed_parameters=json.loads(m["proposed_parameters_json"]),
        proposed_factor_refs=tuple(json.loads(m["proposed_factor_refs_json"])),
        rationale=m["rationale"],
        status=ChangeRequestStatus(m["status"]),
        created_by=m["created_by"],
        created_at=m["created_at"],
        derived_version=m["derived_version"],
        decided_by=m["decided_by"],
        decided_at=m["decided_at"],
        rejection_reason=m["rejection_reason"],
    )


def _row_to_eval_run(row: Any) -> EvaluationRun:
    m = row._mapping
    return EvaluationRun(
        run_id=m["run_id"],
        strategy_id=m["strategy_id"],
        version=m["version"],
        status=EvaluationStatus(m["status"]),
        window_start=m["window_start"],
        window_end=m["window_end"],
        regime_slices=tuple(json.loads(m["regime_slices_json"])),
        metrics=json.loads(m["metrics_json"]),
        started_by=m["started_by"],
        started_at=m["started_at"],
        completed_at=m["completed_at"],
        repro_hash=m["repro_hash"],
        failure_reason=m["failure_reason"],
    )


# --------------------------------------------------------------------------- #
# SQL repositories
# --------------------------------------------------------------------------- #
class SqlParameterSetRepository:
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def get(self, content_hash: str) -> ParameterSetVersion | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(parameter_set_version_t).where(
                parameter_set_version_t.c.content_hash == content_hash
            )).first()
        return _row_to_param_set(row) if row else None

    def save(self, ps: ParameterSetVersion) -> None:
        with self._engine.begin() as conn:
            try:
                conn.execute(sa.insert(parameter_set_version_t).values(
                    content_hash=ps.content_hash,
                    values_json=json.dumps(ps.values, sort_keys=True, default=str),
                    schema_version=ps.schema_version,
                    created_by=ps.created_by,
                    created_at=ps.created_at,
                ))
            except sa.exc.IntegrityError:
                # content_hash collision = idempotent re-save of immutable set
                pass


class SqlStrategyVersionRepository:
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine
        self._params = SqlParameterSetRepository(engine)

    def get(self, strategy_id: str, version: str) -> StrategyVersion | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(strategy_version_t).where(
                strategy_version_t.c.strategy_id == strategy_id,
                strategy_version_t.c.version == version,
            )).first()
        if row is None:
            return None
        ps = self._params.get(row._mapping["parameter_set_hash"])
        if ps is None:
            return None
        return _row_to_strategy_version(row, ps)

    def get_latest(self, strategy_id: str) -> StrategyVersion | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(strategy_version_t).where(
                strategy_version_t.c.strategy_id == strategy_id
            ).order_by(strategy_version_t.c.created_at.desc()).limit(1)).first()
        if row is None:
            return None
        return self.get(row._mapping["strategy_id"], row._mapping["version"])

    def get_production(self, strategy_id: str) -> StrategyVersion | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(strategy_version_t).where(
                strategy_version_t.c.strategy_id == strategy_id,
                strategy_version_t.c.state == StrategyState.PRODUCTION.value,
            ).limit(1)).first()
        if row is None:
            return None
        return self.get(row._mapping["strategy_id"], row._mapping["version"])

    def save(self, version: StrategyVersion) -> None:
        # parameter set must exist first (FK)
        self._params.save(version.parameter_set)
        with self._engine.begin() as conn:
            try:
                conn.execute(sa.insert(strategy_version_t).values(
                    strategy_id=version.strategy_id,
                    version=version.version,
                    parent_version=version.parent_version,
                    market=version.market.value,
                    horizon_days=version.horizon_days,
                    state=version.state.value,
                    parameter_set_hash=version.parameter_set.content_hash,
                    feature_set_hash=version.feature_set_hash,
                    factor_refs_json=json.dumps(list(version.factor_refs)),
                    model_ref=version.model_ref,
                    code_commit=version.code_commit,
                    config_hash=version.config_hash,
                    created_by=version.created_by,
                    created_at=version.created_at,
                    approved_by=version.approved_by,
                    approval_id=version.approval_id,
                ))
            except sa.exc.IntegrityError:
                # (strategy_id, version) collision = idempotent re-save
                pass

    def compare_and_set_state(
        self,
        strategy_id: str,
        version: str,
        expected_from: StrategyState,
        to: StrategyState,
        *,
        approved_by: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.update(strategy_version_t).where(
                    strategy_version_t.c.strategy_id == strategy_id,
                    strategy_version_t.c.version == version,
                    strategy_version_t.c.state == expected_from.value,
                ).values(
                    state=to.value,
                    approved_by=approved_by,
                    approval_id=approval_id,
                )
            )
            return result.rowcount == 1

    def list_by_strategy(self, strategy_id: str) -> list[StrategyVersion]:
        with self._engine.connect() as conn:
            rows = conn.execute(sa.select(strategy_version_t).where(
                strategy_version_t.c.strategy_id == strategy_id
            ).order_by(strategy_version_t.c.created_at.desc())).all()
        out: list[StrategyVersion] = []
        for r in rows:
            v = self.get(r._mapping["strategy_id"], r._mapping["version"])
            if v is not None:
                out.append(v)
        return out


class SqlChangeRequestRepository:
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def get(self, request_id: str) -> ChangeRequest | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(change_request_t).where(
                change_request_t.c.request_id == request_id
            )).first()
        return _row_to_change_request(row) if row else None

    def save(self, req: ChangeRequest) -> None:
        with self._engine.begin() as conn:
            try:
                conn.execute(sa.insert(change_request_t).values(
                    request_id=req.request_id,
                    strategy_id=req.strategy_id,
                    parent_version=req.parent_version,
                    proposed_parameters_json=json.dumps(req.proposed_parameters, sort_keys=True, default=str),
                    proposed_factor_refs_json=json.dumps(list(req.proposed_factor_refs)),
                    rationale=req.rationale,
                    status=req.status.value,
                    created_by=req.created_by,
                    created_at=req.created_at,
                    derived_version=req.derived_version,
                    decided_by=req.decided_by,
                    decided_at=req.decided_at,
                    rejection_reason=req.rejection_reason,
                ))
            except sa.exc.IntegrityError:
                pass

    def update_status(
        self,
        request_id: str,
        status: str,
        *,
        derived_version: str | None = None,
        decided_by: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool:
        vals: dict[str, Any] = {
            "status": status,
            "decided_at": utcnow(),
        }
        if derived_version is not None:
            vals["derived_version"] = derived_version
        if decided_by is not None:
            vals["decided_by"] = decided_by
        if rejection_reason is not None:
            vals["rejection_reason"] = rejection_reason
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.update(change_request_t).where(
                    change_request_t.c.request_id == request_id
                ).values(**vals)
            )
            return result.rowcount == 1

    def list_by_strategy(self, strategy_id: str) -> list[ChangeRequest]:
        with self._engine.connect() as conn:
            rows = conn.execute(sa.select(change_request_t).where(
                change_request_t.c.strategy_id == strategy_id
            ).order_by(change_request_t.c.created_at.desc())).all()
        return [_row_to_change_request(r) for r in rows]


class SqlEvaluationRunRepository:
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def get(self, run_id: str) -> EvaluationRun | None:
        with self._engine.connect() as conn:
            row = conn.execute(sa.select(evaluation_run_t).where(
                evaluation_run_t.c.run_id == run_id
            )).first()
        return _row_to_eval_run(row) if row else None

    def save(self, run: EvaluationRun) -> None:
        with self._engine.begin() as conn:
            try:
                conn.execute(sa.insert(evaluation_run_t).values(
                    run_id=run.run_id,
                    strategy_id=run.strategy_id,
                    version=run.version,
                    status=run.status.value,
                    window_start=run.window_start,
                    window_end=run.window_end,
                    regime_slices_json=json.dumps(list(run.regime_slices)),
                    metrics_json=json.dumps(run.metrics, sort_keys=True, default=str),
                    started_by=run.started_by,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    repro_hash=run.repro_hash,
                    failure_reason=run.failure_reason,
                ))
            except sa.exc.IntegrityError:
                pass

    def list_for_version(self, strategy_id: str,
                         version: str) -> list[EvaluationRun]:
        with self._engine.connect() as conn:
            rows = conn.execute(sa.select(evaluation_run_t).where(
                evaluation_run_t.c.strategy_id == strategy_id,
                evaluation_run_t.c.version == version,
            ).order_by(evaluation_run_t.c.started_at.desc())).all()
        return [_row_to_eval_run(r) for r in rows]


class SqlPromotionDecisionRepository:
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def save(self, decision: PromotionDecision) -> None:
        with self._engine.begin() as conn:
            try:
                conn.execute(sa.insert(promotion_decision_t).values(
                    decision_id=decision.decision_id,
                    strategy_id=decision.strategy_id,
                    version=decision.version,
                    from_state=decision.from_state.value,
                    to_state=decision.to_state.value,
                    outcome=decision.outcome.value,
                    evaluation_run_id=decision.evaluation_run_id,
                    decided_by=decision.decided_by,
                    approval_id=decision.approval_id,
                    decided_at=decision.decided_at,
                    reason=decision.reason,
                ))
            except sa.exc.IntegrityError:
                pass

    def list_for_version(self, strategy_id: str,
                         version: str) -> list[PromotionDecision]:
        with self._engine.connect() as conn:
            rows = conn.execute(sa.select(promotion_decision_t).where(
                promotion_decision_t.c.strategy_id == strategy_id,
                promotion_decision_t.c.version == version,
            ).order_by(promotion_decision_t.c.decided_at.desc())).all()
        out: list[PromotionDecision] = []
        for r in rows:
            m = r._mapping
            out.append(PromotionDecision(
                decision_id=m["decision_id"],
                strategy_id=m["strategy_id"],
                version=m["version"],
                from_state=StrategyState(m["from_state"]),
                to_state=StrategyState(m["to_state"]),
                outcome=PromotionOutcome(m["outcome"]),
                evaluation_run_id=m["evaluation_run_id"],
                decided_by=m["decided_by"],
                approval_id=m["approval_id"],
                decided_at=m["decided_at"],
                reason=m["reason"],
            ))
        return out
