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
from datetime import date
from typing import Any, Sequence

from packages.audit.record import AuditEvent, AuditSink
from packages.common.time_utils import utcnow
from packages.data_sources.contracts import Bar
from packages.strategy_governance.domain import (
    ChangeRequest,
    ChangeRequestStatus,
    EvaluationRun,
    EvaluationStatus,
    FactorState,
    FactorVersion,
    ParameterSetVersion,
    PromotionDecision,
    PromotionOutcome,
    StrategyState,
    StrategyVersion,
)
from packages.strategy_governance.evaluator import (
    EvaluationResult,
    StrategyEvaluator,
)
from packages.strategy_governance.errors import (
    IllegalTransitionError,
    StrategyGovernanceError,
)
from packages.strategy_governance.policy import (
    ParamSpec,
    compute_change_diff,
    filter_available_factors,
    validate_factor_availability,
    validate_parameter_schema,
    validate_transition,
)
from packages.strategy_governance.repositories import (
    ChangeRequestRepository,
    EvaluationRunRepository,
    FactorVersionRepository,
    PromotionDecisionRepository,
    StrategyVersionRepository,
)
from packages.strategy_governance.shadow import (
    DriftAutoSuspender,
    ShadowTracker,
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
        evaluators: dict[str, StrategyEvaluator] | None = None,
        factors: "FactorVersionRepository | None" = None,
        shadow_tracker: "ShadowTracker | None" = None,
        drift_suspender: "DriftAutoSuspender | None" = None,
    ) -> None:
        self._v = versions
        self._cr = change_requests
        self._er = eval_runs
        self._pd = decisions
        self._audit = audit
        self._schemas = schemas
        # Phase 3: per-strategy walk-forward evaluators (issue #10 §11).
        self._evaluators = evaluators or {}
        # Phase 4: factor governance (issue #10 §11).
        self._factors = factors
        # Phase 5: shadow forward-tracking + drift auto-suspend (issue #10 §11).
        self._shadow = shadow_tracker or ShadowTracker()
        self._drift_suspender = drift_suspender or DriftAutoSuspender()

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
    # Phase 3: run the wired walk-forward evaluator (issue #10 §11)
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        *,
        strategy_id: str,
        version: str,
        bars: Sequence[Bar],
        baseline_metrics: dict[str, dict[str, float]] | None = None,
        gate_keys: tuple[str, ...] = ("ic", "net_return"),
        factor_versions: list[FactorVersion] | None = None,
    ) -> tuple[EvaluationRun, EvaluationResult]:
        """Run the wired walk-forward evaluator and persist the result.

        Returns (EvaluationRun, EvaluationResult). The EvaluationRun.metrics
        carries ``baseline_gate`` (1.0/0.0) so the SHADOW transition can gate
        on 'candidate beat baselines' (§81.1).
        """
        evaluator = self._evaluators.get(strategy_id)
        if evaluator is None:
            raise StrategyGovernanceError(
                f"no evaluator wired for strategy {strategy_id!r}"
            )
        vobj = self._v.get(strategy_id, version)
        if vobj is None:
            raise StrategyGovernanceError(
                f"unknown strategy version {strategy_id}@{version}"
            )
        result = evaluator.evaluate(
            bars, strategy_id=strategy_id, version=version,
            params=vobj.parameter_set.values,
            baseline_metrics=baseline_metrics, gate_keys=gate_keys,
            factor_versions=factor_versions,
        )
        metrics = result.as_metrics()
        if result.baseline_gate_passed is not None:
            metrics["baseline_gate"] = 1.0 if result.baseline_gate_passed else 0.0
        run = EvaluationRun(
            run_id=f"er_{uuid.uuid4().hex[:12]}",
            strategy_id=strategy_id, version=version,
            status=EvaluationStatus.COMPLETED,
            window_start=result.folds[0].test_start if result.folds else "",
            window_end=result.folds[-1].test_end if result.folds else "",
            regime_slices=tuple(sorted(result.regime_ic.keys())),
            metrics=metrics, started_by="evaluator",
            completed_at=utcnow(), repro_hash=result.repro_hash,
        )
        self._er.save(run)
        self._audit.insert(self._event(
            "evaluator", "service", "strategy.evaluation.completed",
            resource_id=run.run_id, resource_type="evaluation_run",
            metadata={"strategy_id": strategy_id, "version": version,
                      "ic": result.ic, "n_folds": result.n_folds,
                      "baseline_gate_passed": result.baseline_gate_passed},
        ))
        return run, result

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

        # 1b. Phase 3 baseline gate for SHADOW (§81.1): the candidate must have
        # beaten baselines on its latest walk-forward eval. The eval's metrics
        # carry baseline_gate (1.0=passed); absent/failed = fail-closed.
        if to is StrategyState.SHADOW and runs:
            latest = runs[0]   # list_for_version returns newest-first
            if latest.metrics.get("baseline_gate", 0.0) < 1.0:
                raise StrategyGovernanceError(
                    f"{strategy_id}@{version} -> SHADOW blocked: candidate did "
                    f"not beat baselines (baseline_gate="
                    f"{latest.metrics.get('baseline_gate')!r})"
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
    # Phase 4: factor governance (issue #10 §11)
    # ------------------------------------------------------------------ #
    def _require_factors(self) -> "FactorVersionRepository":
        if self._factors is None:
            raise StrategyGovernanceError(
                "factor governance not wired on this service"
            )
        return self._factors

    def register_factor(
        self,
        *,
        factor_id: str,
        version: str,
        definition_hash: str,
        available_from: "date",
        dependencies: tuple[str, ...] = (),
        description: str = "",
        created_by: str = "system",
    ) -> FactorVersion:
        """Register an immutable factor version with PIT availability.

        ``available_from`` is the date from which this factor's data exists;
        using it at an earlier as_of is a future-function leak rejected by
        the policy layer and the evaluator's per-fold filter.
        """
        fr = self._require_factors()
        factor = FactorVersion(
            factor_id=factor_id, version=version,
            definition_hash=definition_hash, available_from=available_from,
            dependencies=dependencies, description=description,
            state=FactorState.ACTIVE, created_by=created_by,
        )
        fr.save(factor)
        self._audit.insert(self._event(
            created_by, "service", "factor.registered",
            resource_id=f"{factor_id}@{version}", resource_type="factor_version",
            metadata={"available_from": available_from.isoformat(),
                      "dependencies": list(dependencies)},
        ))
        return factor

    def retire_factor(self, *, factor_id: str, version: str,
                      decided_by: str, reason: str) -> bool:
        """Retire a factor (ACTIVE -> RETIRED). Future evaluations will not
        use it. Returns True if the CAS succeeded."""
        fr = self._require_factors()
        ok = fr.retire(factor_id, version)
        self._audit.insert(self._event(
            decided_by, "service", "factor.retired",
            resource_id=f"{factor_id}@{version}", resource_type="factor_version",
            metadata={"reason": reason, "ok": ok},
        ))
        return ok

    def list_factors(self) -> list[FactorVersion]:
        fr = self._require_factors()
        return fr.list_all_active()

    def get_available_factors(self, as_of: "date") -> list[FactorVersion]:
        """PIT filter: all ACTIVE factors whose data exists at ``as_of``."""
        fr = self._require_factors()
        return fr.list_available_at(as_of)

    def check_factor_leakage(
        self,
        *,
        as_of: "date",
        factor_ids: list[str],
    ) -> list[str]:
        """Return the subset of ``factor_ids`` that would LEAK at ``as_of``
        (i.e. their available_from > as_of). Empty list = clean."""
        fr = self._require_factors()
        available = {f.factor_id for f in fr.list_available_at(as_of)}
        return [fid for fid in factor_ids if fid not in available]

    def check_incremental_contribution(
        self,
        *,
        strategy_id: str,
        version: str,
        bars: Sequence[Bar],
        factor_id: str,
        factor_versions: list[FactorVersion],
    ) -> dict[str, Any]:
        """Measure a factor's incremental IC: full set vs set-minus-factor.

        Runs the wired evaluator twice (with and without ``factor_id``) and
        returns the IC delta. A negative delta means the factor HURTS — it
        should not be registered / should be retired.
        """
        evaluator = self._evaluators.get(strategy_id)
        if evaluator is None:
            raise StrategyGovernanceError(
                f"no evaluator wired for strategy {strategy_id!r}"
            )
        full = evaluator.evaluate(
            bars, strategy_id=strategy_id, version=version,
            factor_versions=factor_versions,
        )
        without = [f for f in factor_versions if f.factor_id != factor_id]
        reduced = evaluator.evaluate(
            bars, strategy_id=strategy_id, version=version,
            factor_versions=without,
        )
        return {
            "factor_id": factor_id,
            "ic_full": full.ic,
            "ic_without": reduced.ic,
            "incremental_ic": full.ic - reduced.ic,
        }

    # ------------------------------------------------------------------ #
    # Phase 5: shadow forward-tracking + drift auto-suspend (issue #10 §11)
    # ------------------------------------------------------------------ #
    def start_shadow(
        self, *, strategy_id: str, version: str, decided_by: str,
        approval_id: str | None = None, reason: str | None = None,
    ) -> StrategyVersion:
        """Transition a BACKTESTED version to SHADOW and begin forward-tracking.

        From this point, every prediction the strategy makes is recorded; once
        its horizon elapses it is settled against the realised return. The
        resulting live track record is the honest measure of 'does it still
        work now' — not the walk-forward that got it here.
        """
        return self.transition(strategy_id=strategy_id, version=version,
                               to=StrategyState.SHADOW, decided_by=decided_by,
                               approval_id=approval_id, reason=reason)

    def record_shadow_prediction(
        self, *, strategy_id: str, version: str, instrument_id: str,
        as_of: "date", horizon_days: int, expected_return: float,
        model_ref: str | None = None,
    ) -> "ShadowPrediction":
        """Record a live prediction. Stored white-on-black; settled later."""
        return self._shadow.record_prediction(
            strategy_id=strategy_id, version=version,
            instrument_id=instrument_id, as_of=as_of,
            horizon_days=horizon_days, expected_return=expected_return,
            model_ref=model_ref,
        )

    def settle_shadow_outcomes(
        self, *, as_of: "date", actual_return_fn,
    ) -> list:
        """Settle predictions whose horizon has elapsed by ``as_of``."""
        return self._shadow.settle_due(as_of=as_of,
                                       actual_return_fn=actual_return_fn)

    def get_live_track_record(self, *, strategy_id: str,
                              recent_n: int = 50) -> dict[str, float]:
        """Honest out-of-sample accuracy of recent settled shadow predictions."""
        return self._shadow.live_track_record(strategy_id=strategy_id,
                                              recent_n=recent_n)

    def check_drift_and_suspend(
        self, *, strategy_id: str, version: str,
        decided_by: str = "drift_monitor",
    ) -> tuple[bool, str]:
        """Check the live track record and auto-suspend if drifting.

        This is the fail-closed safety net: a collapsed live IC or breached
        drawdown suspends the strategy without waiting for a human. Returns
        (suspended, reason)."""
        return self._drift_suspender.check_and_suspend(
            tracker=self._shadow, strategy_id=strategy_id, version=version,
            service=self, decided_by=decided_by,
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
