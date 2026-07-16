"""Admin/write MCP tools — spec §94 (13 tools).

Every mutation goes through :class:`packages.audit.record.AuditLog`. Dual
control (``approval_id``) is enforced for high-risk operations. Long-running
work (ingestion / features / datasets / backtest / training) is modeled as
queued jobs — the tools return ``job_id`` immediately and callers poll via
``job_get_status``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from packages.audit.record import AuditLog
from packages.common.errors import ErrorCode, QuantError
from packages.common.instrument_id import Market
from packages.common.response import err, ok
from packages.common.time_utils import utcnow
from packages.evaluation.promotion import beats_all_baselines
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState, ModelTransitionError,
)
from packages.strategy_governance.service import StrategyGovernanceService


TOOL_MANIFEST: list[dict[str, Any]] = [
    {"name": "ingestion_create_job", "read_only": False, "requires_approval": False,
     "description": "Queue a data-ingestion job for a (market, dataset, date_range)."},
    {"name": "feature_create_job", "read_only": False, "requires_approval": False,
     "description": "Queue a feature-computation job pinned to a dataset snapshot."},
    {"name": "dataset_create_snapshot", "read_only": False, "requires_approval": False,
     "description": "Create an immutable dataset snapshot with SHA-256 identity."},
    {"name": "backtest_create_job", "read_only": False, "requires_approval": False,
     "description": "Queue an event-driven backtest run."},
    {"name": "training_create_job", "read_only": False, "requires_approval": False,
     "description": "Queue a walk-forward training run for a model spec."},
    {"name": "job_get_status", "read_only": True, "requires_approval": False,
     "description": "Return the state of a previously queued job."},
    {"name": "model_compare", "read_only": True, "requires_approval": False,
     "description": "Return side-by-side metrics for two models."},
    {"name": "model_start_shadow", "read_only": False, "requires_approval": False,
     "description": "Promote a CANDIDATE model into SHADOW state."},
    {"name": "model_request_promotion", "read_only": False, "requires_approval": True,
     "description": "Open a PRODUCTION-promotion request awaiting a second approver."},
    {"name": "model_approve_promotion", "read_only": False, "requires_approval": True,
     "description": "Second approver commits the promotion; requires distinct actor."},
    {"name": "model_request_rollback", "read_only": False, "requires_approval": True,
     "description": "Retire current PRODUCTION model. Dual-control."},
    {"name": "risk_policy_validate", "read_only": True, "requires_approval": False,
     "description": "Validate a proposed risk-policy YAML against invariants."},
    {"name": "audit_query", "read_only": True, "requires_approval": False,
     "description": "Query the append-only audit log by actor / resource / time range."},
]


class JobType(str, Enum):
    INGESTION = "INGESTION"
    FEATURE = "FEATURE"
    DATASET = "DATASET"
    BACKTEST = "BACKTEST"
    TRAINING = "TRAINING"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass
class Job:
    job_id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    payload: dict[str, Any]
    result: dict[str, Any] | None = None


@dataclass
class PendingPromotion:
    request_id: str
    model_id: str
    version: str
    requested_by: str
    created_at: datetime
    approvers: set[str] = field(default_factory=set)


def _audit(log: AuditLog, *, actor: str, action: str,
           resource_type: str, resource_id: str,
           metadata: dict[str, Any] | None = None,
           approval_id: str | None = None) -> None:
    log.record(
        actor_id=actor, actor_type="human",
        action=action,
        resource_type=resource_type, resource_id=resource_id,
        approval_id=approval_id,
        metadata=metadata or {},
    )


def _err_of(code: str, message: str) -> dict:
    e = QuantError(message)
    try:
        e.code = ErrorCode(code)
    except ValueError:
        e.code = ErrorCode.INTERNAL_ERROR
    return err(e)


class AdminTools:
    def __init__(self, *, registry: InMemoryModelRegistry, audit: AuditLog,
                 job_store: "SqlJobStore | None" = None,
                 governance: "StrategyGovernanceService | None" = None) -> None:
        self._registry = registry
        self._audit = audit
        self._kill_switch = False
        self._jobs: dict[str, Job] = {}
        self._pending_promotions: dict[str, PendingPromotion] = {}
        # issue #3: durable job state — when wired, jobs survive restart.
        self._job_store = job_store
        # issue #10 Phase 2: strategy governance — LLM may propose + read only.
        self._governance = governance

    # ---- 1-5: long-running job creators -----------------------------------

    def _queue(self, job_type: JobType, payload: dict[str, Any], actor: str) -> Job:
        job = Job(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            job_type=job_type,
            status=JobStatus.QUEUED,
            created_at=utcnow(),
            payload=payload,
        )
        self._jobs[job.job_id] = job
        if self._job_store is not None:
            import json
            self._job_store.create(
                job_id=job.job_id, kind=job.job_type.value,
                payload_json=json.dumps(payload, default=str))
        _audit(self._audit, actor=actor, action=f"job.create.{job_type.value.lower()}",
               resource_type="job", resource_id=job.job_id,
               metadata={"payload_keys": list(payload.keys())})
        return job

    def ingestion_create_job(self, *, market: str, dataset: str, from_date: str,
                             to_date: str, actor: str) -> dict:
        try:
            Market(market)
        except ValueError:
            return _err_of("UNKNOWN_INSTRUMENT", f"unknown market {market!r}")
        job = self._queue(JobType.INGESTION,
                          {"market": market, "dataset": dataset,
                           "from": from_date, "to": to_date}, actor)
        return ok({"job_id": job.job_id, "job_type": job.job_type.value,
                   "status": job.status.value, "created_at": job.created_at.isoformat()})

    def feature_create_job(self, *, dataset_snapshot_id: str, feature_set_version: str,
                           actor: str) -> dict:
        job = self._queue(JobType.FEATURE,
                          {"dataset_snapshot_id": dataset_snapshot_id,
                           "feature_set_version": feature_set_version}, actor)
        return ok({"job_id": job.job_id, "status": job.status.value})

    def dataset_create_snapshot(self, *, dataset_name: str, universe: str,
                                as_of_date: str, actor: str) -> dict:
        job = self._queue(JobType.DATASET,
                          {"dataset_name": dataset_name, "universe": universe,
                           "as_of_date": as_of_date}, actor)
        return ok({"job_id": job.job_id, "status": job.status.value})

    def backtest_create_job(self, *, model_id: str, version: str,
                            from_date: str, to_date: str, actor: str) -> dict:
        job = self._queue(JobType.BACKTEST,
                          {"model_id": model_id, "version": version,
                           "from": from_date, "to": to_date}, actor)
        return ok({"job_id": job.job_id, "status": job.status.value})

    def training_create_job(self, *, model_spec: str, dataset_snapshot_id: str,
                            actor: str) -> dict:
        job = self._queue(JobType.TRAINING,
                          {"model_spec": model_spec,
                           "dataset_snapshot_id": dataset_snapshot_id}, actor)
        return ok({"job_id": job.job_id, "status": job.status.value})

    # ---- 6: job_get_status ------------------------------------------------

    def job_get_status(self, *, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        # issue #3: restart recovery — rehydrate from durable store if missing
        if job is None and self._job_store is not None:
            row = self._job_store.get(job_id)
            if row is not None:
                job = Job(
                    job_id=row["job_id"],
                    job_type=JobType(row["kind"]),
                    status=JobStatus(row["status"]),
                    created_at=row["created_at_utc"],
                    payload={},
                )
                self._jobs[job_id] = job
        if job is None:
            return _err_of("UNKNOWN_INSTRUMENT", f"unknown job {job_id!r}")
        return ok({"job_id": job.job_id, "job_type": job.job_type.value,
                   "status": job.status.value,
                   "created_at": job.created_at.isoformat(),
                   "result": job.result})

    def _mark_job(self, job_id: str, status: JobStatus,
                  result: dict[str, Any] | None = None) -> None:
        """Test/ops helper — flip a job's status."""
        job = self._jobs[job_id]
        self._jobs[job_id] = replace(job, status=status, result=result)
        # issue #3: mirror to durable store so status survives restart
        if self._job_store is not None:
            import json
            self._job_store.update(
                job_id, status=status.value,
                result_json=json.dumps(result, default=str) if result else None)

    # ---- 7: model_compare -------------------------------------------------

    def model_compare(self, *, a_model_id: str, a_version: str,
                      b_model_id: str, b_version: str) -> dict:
        a = self._registry._by_key.get((a_model_id, a_version))  # type: ignore[attr-defined]
        b = self._registry._by_key.get((b_model_id, b_version))  # type: ignore[attr-defined]
        if a is None or b is None:
            return _err_of("MODEL_NOT_AVAILABLE", "one or both models not registered")
        return ok({
            "a": {"model_id": a.model_id, "version": a.version,
                  "state": a.state.value, "metrics": a.metrics},
            "b": {"model_id": b.model_id, "version": b.version,
                  "state": b.state.value, "metrics": b.metrics},
        })

    # ---- 8: model_start_shadow -------------------------------------------

    def model_start_shadow(self, *, model_id: str, version: str, actor: str,
                            candidate_metrics: dict[str, float] | None = None,
                            baseline_metrics: dict[str, dict[str, float]] | None = None,
                            ) -> dict:
        # §81.1: SHADOW promotion requires candidate to beat every baseline.
        gate = None
        if candidate_metrics is not None and baseline_metrics is not None:
            gate = beats_all_baselines(
                candidate_id=f"{model_id}@{version}",
                candidate_metrics=candidate_metrics,
                baselines=baseline_metrics,
            )
        try:
            rec = self._registry.transition(
                model_id, version, ModelState.SHADOW, actor=actor,
                promotion_gate=gate,
            )
        except ModelTransitionError as e:
            return _err_of("MODEL_TRANSITION_FAILED", str(e))
        _audit(self._audit, actor=actor, action="model.start_shadow",
               resource_type="model", resource_id=f"{model_id}@{version}",
               metadata={"gate_passed": bool(gate and gate.passed)})
        return ok({"model_id": model_id, "version": version, "state": rec.state.value})

    # ---- 9: model_request_promotion --------------------------------------

    def model_request_promotion(self, *, model_id: str, version: str,
                                actor: str) -> dict:
        key = (model_id, version)
        rec = self._registry._by_key.get(key)  # type: ignore[attr-defined]
        if rec is None:
            return _err_of("MODEL_NOT_AVAILABLE", f"unknown model {model_id}@{version}")
        req = PendingPromotion(
            request_id=f"promo_{uuid.uuid4().hex[:10]}",
            model_id=model_id, version=version,
            requested_by=actor, created_at=utcnow(),
            approvers={actor},
        )
        self._pending_promotions[req.request_id] = req
        _audit(self._audit, actor=actor, action="model.request_promotion",
               resource_type="model", resource_id=f"{model_id}@{version}",
               metadata={"request_id": req.request_id})
        return ok({"request_id": req.request_id, "approvers": list(req.approvers)})

    # ---- 10: model_approve_promotion -------------------------------------

    def model_approve_promotion(self, *, request_id: str, actor: str,
                                approval_id: str,
                                candidate_metrics: dict[str, float] | None = None,
                                baseline_metrics: dict[str, dict[str, float]] | None = None,
                                ) -> dict:
        req = self._pending_promotions.get(request_id)
        if req is None:
            return _err_of("UNKNOWN_INSTRUMENT", f"unknown request {request_id!r}")
        if actor == req.requested_by:
            return _err_of("APPROVAL_REQUIRED",
                           "second approver must differ from requester")
        req.approvers.add(actor)
        # §81.1: build gate if metrics provided; when transitioning from
        # SHADOW the record already carries prior gate evidence but a fresh
        # comparison is still preferred. Registry only requires the gate on
        # CANDIDATE->PRODUCTION, so this is a no-op when starting from SHADOW.
        gate = None
        if candidate_metrics is not None and baseline_metrics is not None:
            gate = beats_all_baselines(
                candidate_id=f"{req.model_id}@{req.version}",
                candidate_metrics=candidate_metrics,
                baselines=baseline_metrics,
            )
        try:
            rec = self._registry.transition(
                req.model_id, req.version, ModelState.PRODUCTION,
                actor=actor, approval_id=approval_id, promotion_gate=gate,
            )
        except ModelTransitionError as e:
            return _err_of("MODEL_TRANSITION_FAILED", str(e))
        _audit(self._audit, actor=actor, action="model.approve_promotion",
               resource_type="model", resource_id=f"{req.model_id}@{req.version}",
               approval_id=approval_id,
               metadata={"request_id": request_id,
                         "approvers": sorted(req.approvers),
                         "gate_passed": bool(gate and gate.passed)})
        del self._pending_promotions[request_id]
        return ok({"model_id": rec.model_id, "version": rec.version,
                   "state": rec.state.value})

    # ---- 11: model_request_rollback --------------------------------------

    def model_request_rollback(self, *, market: str, horizon_days: int,
                               actor: str, approval_id: str) -> dict:
        if not approval_id:
            return _err_of("APPROVAL_REQUIRED", "rollback requires approval_id")
        try:
            mkt = Market(market)
        except ValueError:
            return _err_of("UNKNOWN_INSTRUMENT", f"unknown market {market!r}")
        cur = self._registry.get_production(mkt, horizon_days)
        if cur is None:
            return _err_of("MODEL_NOT_AVAILABLE", "no PRODUCTION model to rollback")
        try:
            self._registry.transition(cur.model_id, cur.version, ModelState.RETIRED,
                                      actor=actor, approval_id=approval_id)
        except ModelTransitionError as e:
            return _err_of("MODEL_TRANSITION_FAILED", str(e))
        _audit(self._audit, actor=actor, action="model.rollback",
               resource_type="model", resource_id=f"{cur.model_id}@{cur.version}",
               approval_id=approval_id,
               metadata={"market": market, "horizon_days": horizon_days})
        return ok({"retired": {"model_id": cur.model_id, "version": cur.version}})

    # ---- 12: risk_policy_validate ----------------------------------------

    def risk_policy_validate(self, *, policy: dict[str, Any]) -> dict:
        """Validate structural invariants of a proposed risk policy.

        This is intentionally *pure* — the tool cannot mutate live policy.
        Callers submit the result to the human review workflow.
        """
        problems: list[str] = []
        for key in ("max_single_equity_weight", "max_single_etf",
                    "max_sector", "max_turnover", "min_cash"):
            if key not in policy:
                problems.append(f"missing {key}")
                continue
            val = policy[key]
            if not isinstance(val, (int, float)):
                problems.append(f"{key} must be numeric")
            elif not (0.0 <= float(val) <= 1.0):
                problems.append(f"{key} must be in [0, 1]")
        if "max_market_weight" in policy:
            mm = policy["max_market_weight"]
            if not isinstance(mm, dict):
                problems.append("max_market_weight must be a map")
            else:
                for k, v in mm.items():
                    if not (0.0 <= float(v) <= 1.0):
                        problems.append(f"max_market_weight[{k}] out of range")
        if problems:
            return ok({"valid": False, "problems": problems})
        return ok({"valid": True, "problems": []})

    # ---- 13: audit_query --------------------------------------------------

    def audit_query(self, *, actor_id: str | None = None,
                    resource_type: str | None = None,
                    resource_id: str | None = None,
                    limit: int = 100) -> dict:
        events = self._audit.events()
        filtered = []
        for e in events:
            if actor_id and e.actor_id != actor_id:
                continue
            if resource_type and e.resource_type != resource_type:
                continue
            if resource_id and e.resource_id != resource_id:
                continue
            filtered.append({
                "actor_id": e.actor_id,
                "actor_type": e.actor_type,
                "action": e.action,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "approval_id": e.approval_id,
                "before_hash": e.before_hash,
                "after_hash": e.after_hash,
                "created_at": e.created_at.isoformat(),
                "metadata": e.metadata,
            })
        return ok({"events": filtered[-limit:], "total": len(filtered)})

    # ---- legacy compat: register/promote/rollback (used by old callers) ---

    def register_model(
        self, *,
        model_id: str, version: str, market: str, horizon_days: int,
        feature_set_hash: str, actor: str, notes: str | None = None,
    ) -> dict:
        try:
            rec = ModelRecord(
                model_id=model_id, version=version,
                market=Market(market), horizon_days=horizon_days,
                feature_set_hash=feature_set_hash,
                state=ModelState.DRAFT,
                created_at=utcnow(),
                approved_by=None, approval_id=None, notes=notes,
            )
            self._registry.register(rec)
        except (ModelTransitionError, ValueError) as e:
            return _err_of("MODEL_REGISTER_FAILED", str(e))

        _audit(self._audit, actor=actor, action="model.register",
               resource_type="model", resource_id=f"{model_id}@{version}",
               metadata={"market": market, "horizon_days": horizon_days,
                         "feature_set_hash": feature_set_hash})
        return ok({"model_id": model_id, "version": version, "state": rec.state.value})

    def promote_model(
        self, *, model_id: str, version: str, to_state: str,
        actor: str, approval_id: str | None = None,
        candidate_metrics: dict[str, float] | None = None,
        baseline_metrics: dict[str, dict[str, float]] | None = None,
    ) -> dict:
        try:
            target = ModelState(to_state)
            gate = None
            if candidate_metrics is not None and baseline_metrics is not None:
                gate = beats_all_baselines(
                    candidate_id=f"{model_id}@{version}",
                    candidate_metrics=candidate_metrics,
                    baselines=baseline_metrics,
                )
            rec = self._registry.transition(
                model_id, version, target,
                actor=actor, approval_id=approval_id, promotion_gate=gate,
            )
        except (ModelTransitionError, ValueError) as e:
            return _err_of("MODEL_TRANSITION_FAILED", str(e))
        _audit(self._audit, actor=actor, action="model.promote",
               resource_type="model", resource_id=f"{model_id}@{version}",
               approval_id=approval_id, metadata={"to_state": to_state,
                   "gate_passed": bool(gate and gate.passed)})
        return ok({"model_id": model_id, "version": version, "state": rec.state.value})

    def rollback_production(
        self, *, market: str, horizon_days: int, actor: str, approval_id: str,
    ) -> dict:
        return self.model_request_rollback(
            market=market, horizon_days=horizon_days,
            actor=actor, approval_id=approval_id,
        )

    def engage_kill_switch(self, *, actor: str, approval_id: str, reason: str) -> dict:
        if not approval_id:
            return _err_of("APPROVAL_REQUIRED", "kill-switch requires approval_id")
        self._kill_switch = True
        _audit(self._audit, actor=actor, action="ops.kill_switch.engage",
               resource_type="system", resource_id="global",
               approval_id=approval_id, metadata={"reason": reason})
        return ok({"kill_switch": True})

    def release_kill_switch(self, *, actor: str, approval_id: str) -> dict:
        if not approval_id:
            return _err_of("APPROVAL_REQUIRED", "kill-switch release requires approval_id")
        self._kill_switch = False
        _audit(self._audit, actor=actor, action="ops.kill_switch.release",
               resource_type="system", resource_id="global",
               approval_id=approval_id, metadata={})
        return ok({"kill_switch": False})

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_switch

    # ---- issue #10 Phase 2: strategy governance (LLM may propose + read) --
    # IMPORTANT: transition/approve/reject/suspend/rollback are deliberately
    # NOT exposed here. Those are human/admin channels. An LLM that somehow
    # obtained the service could still not self-promote: the policy layer
    # rejects transition(..., to=PRODUCTION) without a non-empty approval_id.

    def _require_governance(self) -> StrategyGovernanceService:
        if self._governance is None:
            raise QuantError(ErrorCode.PERMISSION_DENIED,
                             "strategy governance not wired on this AdminTools")
        return self._governance

    def strategy_propose_change(
        self, *, strategy_id: str, parent_version: str | None,
        proposed_parameters: dict[str, Any], proposed_factor_refs: list[str],
        rationale: str, actor_id: str, actor_type: str = "agent",
    ) -> dict:
        """LLM-safe: file a change proposal. Returns PROPOSED change_request.
        Validation/derivation/promotion are separate (non-LLM) steps."""
        g = self._require_governance()
        cr = g.propose_change(
            strategy_id=strategy_id, parent_version=parent_version,
            proposed_parameters=proposed_parameters,
            proposed_factor_refs=tuple(proposed_factor_refs),
            rationale=rationale, actor_id=actor_id, actor_type=actor_type,
        )
        return ok({
            "request_id": cr.request_id, "strategy_id": cr.strategy_id,
            "status": cr.status.value, "parent_version": cr.parent_version,
            "created_by": cr.created_by,
        })

    def strategy_get_version(self, *, strategy_id: str,
                             version: str) -> dict:
        g = self._require_governance()
        v = g.get_version(strategy_id, version)
        if v is None:
            return err(ErrorCode.UNKNOWN_INSTRUMENT,
                       f"unknown strategy version {strategy_id}@{version}")
        return ok(self._version_to_dict(v))

    def strategy_get_production(self, *, strategy_id: str) -> dict:
        g = self._require_governance()
        v = g.get_production(strategy_id)
        if v is None:
            return ok({"strategy_id": strategy_id, "production_version": None})
        return ok(self._version_to_dict(v))

    def strategy_list_versions(self, *, strategy_id: str) -> dict:
        g = self._require_governance()
        vs = g.list_versions(strategy_id)
        return ok({"strategy_id": strategy_id, "versions": [self._version_to_dict(v) for v in vs]})

    def strategy_diff_versions(self, *, strategy_id: str,
                               parent_version: str | None,
                               child_version: str) -> dict:
        g = self._require_governance()
        try:
            diff = g.diff_versions(strategy_id, parent_version, child_version)
        except Exception as e:  # noqa: BLE001
            return err(ErrorCode.UNKNOWN_INSTRUMENT, str(e))
        return ok({"strategy_id": strategy_id, "diff": diff})

    def strategy_list_change_requests(self, *, strategy_id: str) -> dict:
        g = self._require_governance()
        crs = g.list_change_requests(strategy_id)
        return ok({"strategy_id": strategy_id, "change_requests": [
            {"request_id": cr.request_id, "status": cr.status.value,
             "parent_version": cr.parent_version,
             "derived_version": cr.derived_version, "rationale": cr.rationale,
             "created_by": cr.created_by} for cr in crs
        ]})

    def strategy_list_evaluations(self, *, strategy_id: str,
                                  version: str) -> dict:
        g = self._require_governance()
        runs = g.list_evaluations(strategy_id, version)
        return ok({"strategy_id": strategy_id, "version": version, "evaluations": [
            {"run_id": r.run_id, "status": r.status.value,
             "window": [r.window_start, r.window_end],
             "regime_slices": list(r.regime_slices), "metrics": r.metrics,
             "repro_hash": r.repro_hash} for r in runs
        ]})

    def strategy_get_audit_trail(self, *, strategy_id: str,
                                 version: str) -> dict:
        """Promotion decision trail for a strategy version."""
        g = self._require_governance()
        decs = g.list_decisions(strategy_id, version)
        return ok({"strategy_id": strategy_id, "version": version, "decisions": [
            {"decision_id": d.decision_id, "from": d.from_state.value,
             "to": d.to_state.value, "outcome": d.outcome.value,
             "decided_by": d.decided_by, "approval_id": d.approval_id,
             "reason": d.reason} for d in decs
        ]})

    @staticmethod
    def _version_to_dict(v: Any) -> dict[str, Any]:
        return {
            "strategy_id": v.strategy_id, "version": v.version,
            "parent_version": v.parent_version, "market": v.market.value,
            "horizon_days": v.horizon_days, "state": v.state.value,
            "feature_set_hash": v.feature_set_hash,
            "factor_refs": list(v.factor_refs), "model_ref": v.model_ref,
            "created_by": v.created_by, "approved_by": v.approved_by,
            "approval_id": v.approval_id,
            "parameters": v.parameter_set.values,
        }
