"""StrategyGovernanceService — the single use-case orchestration layer.

This is the ONLY place a strategy version's state is allowed to change. Every
transition goes through ``policy.validate_transition`` then the repository's
``compare_and_set_state`` (optimistic lock). An LLM may call ``propose_change``
and ``transition``, but ``transition(..., to=PRODUCTION)`` without a human
``approval_id`` is rejected by the policy — the LLM cannot self-promote.

Every mutating call writes an AuditEvent (with a readable diff where relevant),
so the audit trail answers "who changed what, when, why".
"""
from __future__ import annotations

import uuid
from typing import Any

from packages.audit.record import AuditEvent, AuditSink
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
from packages.strategy_governance.errors import (
    IllegalTransitionError,
    StrategyGovernanceError,
)
from packages.strategy_governance.policy import (
    ParamSpec,
    compute_change_diff,
    validate_parameter_schema,
    validate_transition,
)
from packages.strategy_governance.repositories import (
    ChangeRequestRepository,
    EvaluationRunRepository,
    PromotionDecisionRepository,
    StrategyVersionRepository,
)

# Actor types allowed to APPROVE a promotion to PRODUCTION. An "agent" actor
# may propose and transition non-production states, but the policy layer
# separately requires a non-empty approval_id for PRODUCTION — and this constant
# documents that approvals must come from a human/service, not an agent.
APPROVAL_ACTOR_TYPES = frozenset({"human", "service"})


class StrategyGovernanceService:
    """Use-case orchestration. Holds no rules — delegates to policy."""

    def __init__(
        self,
        *,
        versions: StrategyVersionRepository,
        change_requests: ChangeRequestRepository,
        eval_runs: EvaluationRunRepository,
        decisions: PromotionDecisionRepository,
        audit: AuditSink,
        schemas: dict[str, dict[str, ParamSpec]],
    ) -> None:
        self._v = versions
        self._cr = change_requests
        self._er = eval_runs
        self._pd = decisions
        self._audit = audit
        self._schemas = schemas

    # ------------------------------------------------------------------ #
    # Propose (LLM/human may call)
    # ------------------------------------------------------------------ #
    def propose_change(
        self,
        *,
        strategy_id: str,
        parent_version: str | None,
        proposed_parameters: dict[str, Any],
        proposed_factor_refs: tuple[str, ...],
        rationale: str,
        actor_id: str,
        actor_type: str = "agent",
    ) -> ChangeRequest:
        """File a change proposal. This is the ONLY write an LLM should do.

        ``rationale`` is mandatory — the hypothesis must be stated up front.
        The proposal is PROPOSED; validation/derivation is a separate step.
        """
        if not rationale or not rationale.strip():
            raise StrategyGovernanceError("rationale is required for every change proposal")
        req = ChangeRequest(
            request_id=f"cr_{uuid.uuid4().hex[:12]}",
            strategy_id=strategy_id,
            parent_version=parent_version,
            proposed_parameters=dict(proposed_parameters),
            proposed_factor_refs=tuple(proposed_factor_refs),
            rationale=rationale,
            status=ChangeRequestStatus.PROPOSED,
            created_by=actor_id,
        )
        self._cr.save(req)
        self._audit.insert(self._event(
            actor_id, actor_type, "strategy.change.proposed",
            resource_id=req.request_id, resource_type="change_request",
            metadata={"strategy_id": strategy_id, "parent_version": parent_version,
                      "rationale": rationale},
        ))
        return req

    # ------------------------------------------------------------------ #
    # Validate parameters + derive immutable version (service/human)
    # ------------------------------------------------------------------ #
    def validate_and_derive(
        self,
        *,
        request_id: str,
        version_label: str,
        feature_set_hash: str,
        market: Any = None,
        horizon_days: int | None = None,
        code_commit: str | None = None,
        config_hash: str | None = None,
        decided_by: str = "service",
    ) -> StrategyVersion:
        """Validate the proposed params against the whitelist schema, then
        derive an immutable DRAFT StrategyVersion. Marks the ChangeRequest
        VALIDATED (or REJECTED on schema failure)."""
        req = self._cr.get(request_id)
        if req is None:
            raise StrategyGovernanceError(f"unknown change request {request_id!r}")

        schema = self._schemas.get(req.strategy_id)
        if schema is None:
            # No schema declared = nothing is whitelisted = reject everything.
            self._cr.update_status(request_id, ChangeRequestStatus.REJECTED.value,
                                   decided_by=decided_by,
                                   rejection_reason=f"no schema declared for {req.strategy_id}")
            self._audit.insert(self._event(
                decided_by, "service", "strategy.change.rejected",
                resource_id=request_id, resource_type="change_request",
                metadata={"reason": "no schema"},
            ))
            raise StrategyGovernanceError(
                f"no schema declared for strategy {req.strategy_id!r}"
            )

        try:
            validate_parameter_schema(req.proposed_parameters, schema)
        except StrategyGovernanceError as e:
            self._cr.update_status(request_id, ChangeRequestStatus.REJECTED.value,
                                   decided_by=decided_by, rejection_reason=str(e))
            self._audit.insert(self._event(
                decided_by, "service", "strategy.change.rejected",
                resource_id=request_id, resource_type="change_request",
                metadata={"reason": str(e)},
            ))
            raise

        # Derive the parent (if any) to compute a readable diff.
        parent: StrategyVersion | None = None
        if req.parent_version is not None:
            parent = self._v.get(req.strategy_id, req.parent_version)
        param_set = self._make_param_set(req.proposed_parameters, decided_by)
        if parent is not None:
            new_version = parent.derive(
                version=version_label, parameter_set=param_set,
                feature_set_hash=feature_set_hash,
                factor_refs=req.proposed_factor_refs,
                code_commit=code_commit, config_hash=config_hash,
                created_by=decided_by,
            )
        else:
            if market is None or horizon_days is None:
                raise StrategyGovernanceError(
                    "market and horizon_days are required when deriving the first "
                    "version (no parent)"
                )
            new_version = StrategyVersion(
                strategy_id=req.strategy_id, version=version_label,
                parent_version=None, market=market, horizon_days=horizon_days,
                state=StrategyState.DRAFT, parameter_set=param_set,
                feature_set_hash=feature_set_hash,
                factor_refs=req.proposed_factor_refs, code_commit=code_commit,
                config_hash=config_hash, created_by=decided_by,
            )

        self._v.save(new_version)
        self._cr.update_status(request_id, ChangeRequestStatus.VALIDATED.value,
                               derived_version=version_label, decided_by=decided_by)
        diff = compute_change_diff(parent.parameter_set if parent else None, param_set)
        self._audit.insert(self._event(
            decided_by, "service", "strategy.change.validated",
            resource_id=f"{req.strategy_id}@{version_label}",
            resource_type="strategy_version",
            metadata={"request_id": request_id, "diff": diff},
        ))
        return new_version

    # ------------------------------------------------------------------ #
    # Record an evaluation run
    # ------------------------------------------------------------------ #
    def record_evaluation(
        self,
        *,
        strategy_id: str,
        version: str,
        window_start: str,
        window_end: str,
        regime_slices: tuple[str, ...],
        metrics: dict[str, float],
        repro_hash: str | None = None,
        started_by: str = "eval_svc",
    ) -> EvaluationRun:
        run = EvaluationRun(
            run_id=f"er_{uuid.uuid4().hex[:12]}",
            strategy_id=strategy_id, version=version,
            status=EvaluationStatus.COMPLETED,
            window_start=window_start, window_end=window_end,
            regime_slices=regime_slices, metrics=metrics,
            started_by=started_by, completed_at=utcnow(),
            repro_hash=repro_hash,
        )
        self._er.save(run)
        self._audit.insert(self._event(
            started_by, "service", "strategy.evaluation.completed",
            resource_id=run.run_id, resource_type="evaluation_run",
            metadata={"strategy_id": strategy_id, "version": version,
                      "metrics": metrics, "regime_slices": list(regime_slices)},
        ))
        return run

    # ------------------------------------------------------------------ #
    # THE single transition entry point
    # ------------------------------------------------------------------ #
    def transition(
        self,
        *,
        strategy_id: str,
        version: str,
        to: StrategyState,
        decided_by: str,
        approval_id: str | None = None,
        gate_passed: bool | None = None,
        reason: str | None = None,
        actor_type: str = "service",
    ) -> StrategyVersion:
        """The only way a strategy version's state changes.

        1. Load version + its evaluation runs.
        2. policy.validate_transition (state machine + eval + approval + gate).
        3. repository.compare_and_set_state (optimistic lock; concurrent loss
           raises IllegalTransitionError).
        4. Persist a PromotionDecision (append-only audit).
        5. AuditEvent with readable reason.

        An LLM calling this with ``to=PRODUCTION`` and no ``approval_id`` is
        rejected at step 2 by UnapprovedPromotionError — it cannot self-promote.
        """
        current = self._v.get(strategy_id, version)
        if current is None:
            raise StrategyGovernanceError(f"unknown strategy version {strategy_id}@{version}")
        runs = self._er.list_for_version(strategy_id, version)

        # 1. policy gate (raises on any illegal/gated transition)
        validate_transition(
            current, to,
            evaluation_runs=runs, approval_id=approval_id, gate_passed=gate_passed,
        )

        # 2. optimistic-lock state change
        ok = self._v.compare_and_set_state(
            strategy_id, version, current.state, to,
            approved_by=decided_by if to is StrategyState.PRODUCTION else None,
            approval_id=approval_id if to is StrategyState.PRODUCTION else None,
        )
        if not ok:
            raise IllegalTransitionError(
                f"concurrent modification: {strategy_id}@{version} state changed "
                f"before transition to {to.value} committed"
            )

        # 3. append-only promotion decision
        outcome = (PromotionOutcome.APPROVED
                   if to not in (StrategyState.REJECTED, StrategyState.RETIRED,
                                 StrategyState.SUSPENDED)
                   else PromotionOutcome.REJECTED)
        decision = PromotionDecision(
            decision_id=f"pd_{uuid.uuid4().hex[:12]}",
            strategy_id=strategy_id, version=version,
            from_state=current.state, to_state=to, outcome=outcome,
            evaluation_run_id=runs[0].run_id if runs else None,
            decided_by=decided_by, approval_id=approval_id, reason=reason,
        )
        self._pd.save(decision)

        # 4. audit
        action = ("strategy.promotion.approved" if outcome is PromotionOutcome.APPROVED
                  else "strategy.promotion.rejected")
        self._audit.insert(self._event(
            decided_by, actor_type, action,
            resource_id=f"{strategy_id}@{version}", resource_type="strategy_version",
            approval_id=approval_id,
            metadata={"from": current.state.value, "to": to.value,
                      "decision_id": decision.decision_id, "reason": reason or ""},
        ))
        return self._v.get(strategy_id, version)  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # Convenience: reject / suspend / rollback
    # ------------------------------------------------------------------ #
    def reject(self, *, strategy_id: str, version: str, decided_by: str,
               reason: str) -> StrategyVersion:
        return self.transition(strategy_id=strategy_id, version=version,
                               to=StrategyState.REJECTED, decided_by=decided_by,
                               reason=reason)

    def suspend(self, *, strategy_id: str, version: str, decided_by: str,
                reason: str) -> StrategyVersion:
        return self.transition(strategy_id=strategy_id, version=version,
                               to=StrategyState.SUSPENDED, decided_by=decided_by,
                               reason=reason)

    def rollback(self, *, strategy_id: str, decided_by: str, approval_id: str,
                 reason: str) -> StrategyVersion:
        """Roll the strategy back by retiring the current PRODUCTION version.
        A separate human step must promote a prior stable version (or move to
        all-cash) — this service does not auto-pick a fallback."""
        prod = self._v.get_production(strategy_id)
        if prod is None:
            raise StrategyGovernanceError(
                f"no PRODUCTION version for {strategy_id} to roll back"
            )
        return self.transition(strategy_id=strategy_id, version=prod.version,
                               to=StrategyState.RETIRED, decided_by=decided_by,
                               approval_id=approval_id, reason=reason)

    # ------------------------------------------------------------------ #
    # Read-only queries (LLM-safe)
    # ------------------------------------------------------------------ #
    def get_version(self, strategy_id: str, version: str) -> StrategyVersion | None:
        return self._v.get(strategy_id, version)

    def get_production(self, strategy_id: str) -> StrategyVersion | None:
        return self._v.get_production(strategy_id)

    def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        return self._v.list_by_strategy(strategy_id)

    def list_change_requests(self, strategy_id: str) -> list[ChangeRequest]:
        return self._cr.list_by_strategy(strategy_id)

    def list_evaluations(self, strategy_id: str,
                         version: str) -> list[EvaluationRun]:
        return self._er.list_for_version(strategy_id, version)

    def list_decisions(self, strategy_id: str,
                       version: str) -> list[PromotionDecision]:
        return self._pd.list_for_version(strategy_id, version)

    def diff_versions(self, strategy_id: str,
                      parent_version: str | None,
                      child_version: str) -> list[str]:
        """Readable parameter diff between two versions."""
        parent = self._v.get(strategy_id, parent_version) if parent_version else None
        child = self._v.get(strategy_id, child_version)
        if child is None:
            raise StrategyGovernanceError(f"unknown version {strategy_id}@{child_version}")
        return compute_change_diff(
            parent.parameter_set if parent else None, child.parameter_set,
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _make_param_set(values: dict[str, Any], created_by: str) -> ParameterSetVersion:
        import hashlib
        import json
        chash = hashlib.sha256(
            json.dumps(values, sort_keys=True, default=str).encode()
        ).hexdigest()
        return ParameterSetVersion(
            values=dict(values), schema_version="v1",
            content_hash=chash, created_by=created_by,
        )

    @staticmethod
    def _event(actor_id: str, actor_type: str, action: str, *,
               resource_id: str, resource_type: str,
               approval_id: str | None = None,
               metadata: dict[str, Any] | None = None) -> AuditEvent:
        return AuditEvent(
            actor_id=actor_id, actor_type=actor_type,  # type: ignore[arg-type]
            action=action, resource_type=resource_type, resource_id=resource_id,
            before_hash=None, after_hash=None,
            request_id=None, trace_id=None,
            ip_or_service_identity="strategy_governance_svc",
            approval_id=approval_id, metadata=metadata or {},
        )
