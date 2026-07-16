"""Strategy governance Phase 2 tests (issue #10).

Covers the §12 required cases that fall in Phase 2 scope:
  * LLM direct PRODUCTION request rejected
  * no EvaluationRun rejected
  * no human approval rejected
  * concurrent promotion only one succeeds (CAS)
  * legal transition path passes
  * parameter whitelist schema enforced
  * readable change diff
  * repository round-trip
"""
from __future__ import annotations

import hashlib
import json

import pytest
import sqlalchemy as sa

from packages.audit.record import InMemoryAuditSink
from packages.common.instrument_id import Market
from packages.persistence.strategy_governance import (
    _metadata,
    SqlChangeRequestRepository,
    SqlEvaluationRunRepository,
    SqlPromotionDecisionRepository,
    SqlStrategyVersionRepository,
)
from packages.strategy_governance import (
    ParamSpec,
    StrategyState,
    StrategyVersion,
    ParameterSetVersion,
    EvaluationRun,
    EvaluationStatus,
    ChangeRequest,
    ChangeRequestStatus,
    PromotionDecision,
    PromotionOutcome,
    compute_change_diff,
    validate_parameter_schema,
    validate_transition,
)
from packages.strategy_governance.errors import (
    EvaluationMissingError,
    IllegalTransitionError,
    SchemaValidationError,
    UnapprovedPromotionError,
)
from packages.strategy_governance.service import StrategyGovernanceService


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _chash(v: dict) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()


def _engine():
    eng = sa.create_engine("sqlite:///:memory:")
    _metadata.create_all(eng)
    return eng


def _ps(values: dict | None = None, by: str = "test") -> ParameterSetVersion:
    v = values or {"momentum_window": 30, "max_asset_weight": 0.10}
    return ParameterSetVersion(values=v, schema_version="v1",
                               content_hash=_chash(v), created_by=by)


def _schema() -> dict[str, ParamSpec]:
    return {
        "momentum_window": ParamSpec(type="integer", minimum=10, maximum=120),
        "max_asset_weight": ParamSpec(type="number", minimum=0, maximum=0.15),
    }


def _v1(sid: str = "s1", state: StrategyState = StrategyState.DRAFT) -> StrategyVersion:
    return StrategyVersion(
        strategy_id=sid, version="v1", parent_version=None, market=Market.CN,
        horizon_days=20, state=state, parameter_set=_ps(), feature_set_hash="fs",
    )


def _completed_eval(sid: str = "s1", version: str = "v1") -> EvaluationRun:
    return EvaluationRun(
        run_id="er1", strategy_id=sid, version=version,
        status=EvaluationStatus.COMPLETED, window_start="2023-01-01",
        window_end="2024-01-01", regime_slices=("bull",), metrics={"ic": 0.12},
    )


def _svc(eng=None, sid: str = "s1") -> StrategyGovernanceService:
    eng = eng or _engine()
    return StrategyGovernanceService(
        versions=SqlStrategyVersionRepository(eng),
        change_requests=SqlChangeRequestRepository(eng),
        eval_runs=SqlEvaluationRunRepository(eng),
        decisions=SqlPromotionDecisionRepository(eng),
        audit=InMemoryAuditSink(),
        schemas={sid: _schema()},
    )


# --------------------------------------------------------------------------- #
# 1. transition policy
# --------------------------------------------------------------------------- #
def test_transition_legal_path_passes():
    v = _v1()
    validate_transition(v, StrategyState.VALIDATED)
    validate_transition(v.with_state(StrategyState.VALIDATED),
                        StrategyState.BACKTESTED,
                        evaluation_runs=[_completed_eval()])
    validate_transition(v.with_state(StrategyState.BACKTESTED),
                        StrategyState.SHADOW, evaluation_runs=[_completed_eval()])
    validate_transition(v.with_state(StrategyState.SHADOW),
                        StrategyState.CANARY, gate_passed=True)
    validate_transition(v.with_state(StrategyState.CANARY),
                        StrategyState.PRODUCTION, approval_id="apr-1")


@pytest.mark.parametrize("frm,to,kwargs,exc", [
    (StrategyState.DRAFT, StrategyState.PRODUCTION, {"approval_id": "x"},
     IllegalTransitionError),
    (StrategyState.DRAFT, StrategyState.SHADOW, {}, IllegalTransitionError),
    (StrategyState.VALIDATED, StrategyState.BACKTESTED, {},
     EvaluationMissingError),
    (StrategyState.BACKTESTED, StrategyState.SHADOW, {}, EvaluationMissingError),
    (StrategyState.SHADOW, StrategyState.CANARY, {"gate_passed": False},
     IllegalTransitionError),
    (StrategyState.CANARY, StrategyState.PRODUCTION, {"approval_id": None},
     UnapprovedPromotionError),
    (StrategyState.CANARY, StrategyState.PRODUCTION, {"approval_id": "  "},
     UnapprovedPromotionError),
    (StrategyState.RETIRED, StrategyState.PRODUCTION, {"approval_id": "x"},
     IllegalTransitionError),
])
def test_transition_illegal_or_gated_rejected(frm, to, kwargs, exc):
    v = _v1(state=frm)
    with pytest.raises(exc):
        validate_transition(v, to, **kwargs)


def test_terminal_state_has_no_exit():
    for term in (StrategyState.REJECTED, StrategyState.RETIRED):
        v = _v1(state=term)
        with pytest.raises(IllegalTransitionError):
            validate_transition(v, StrategyState.PRODUCTION, approval_id="x")


# --------------------------------------------------------------------------- #
# 2. parameter schema
# --------------------------------------------------------------------------- #
def test_schema_accepts_valid_params():
    validate_parameter_schema(
        {"momentum_window": 30, "max_asset_weight": 0.10}, _schema())


def test_schema_rejects_missing_required():
    with pytest.raises(SchemaValidationError, match="missing required"):
        validate_parameter_schema({"momentum_window": 30}, _schema())


def test_schema_rejects_out_of_range():
    with pytest.raises(SchemaValidationError, match="below minimum"):
        validate_parameter_schema(
            {"momentum_window": 5, "max_asset_weight": 0.10}, _schema())
    with pytest.raises(SchemaValidationError, match="above maximum"):
        validate_parameter_schema(
            {"momentum_window": 200, "max_asset_weight": 0.10}, _schema())


def test_schema_rejects_wrong_type():
    with pytest.raises(SchemaValidationError, match="must be integer"):
        validate_parameter_schema(
            {"momentum_window": "thirty", "max_asset_weight": 0.10}, _schema())


def test_schema_rejects_undeclared_smuggled_param():
    with pytest.raises(SchemaValidationError, match="undeclared"):
        validate_parameter_schema(
            {"momentum_window": 30, "max_asset_weight": 0.10, "smuggled": 1},
            _schema())


def test_schema_rejects_bool_as_integer():
    with pytest.raises(SchemaValidationError, match="must be integer, got bool"):
        validate_parameter_schema(
            {"momentum_window": True, "max_asset_weight": 0.10}, _schema())


# --------------------------------------------------------------------------- #
# 3. change diff
# --------------------------------------------------------------------------- #
def test_diff_detects_change_add_drop():
    parent = _ps({"momentum_window": 30, "max_asset_weight": 0.10})
    child = _ps({"momentum_window": 40, "max_asset_weight": 0.10, "new_p": 0.05})
    diff = compute_change_diff(parent, child)
    assert any("momentum_window" in l and "30" in l and "40" in l for l in diff)
    assert any("new_p" in l and "added" in l for l in diff)


def test_diff_first_version_all_added():
    child = _ps({"momentum_window": 30})
    diff = compute_change_diff(None, child)
    assert any("momentum_window" in l and "added" in l for l in diff)


def test_diff_no_changes():
    ps = _ps({"momentum_window": 30})
    assert compute_change_diff(ps, ps) == ["(no parameter changes)"]


# --------------------------------------------------------------------------- #
# 4. service: LLM cannot self-promote (the core §12 case)
# --------------------------------------------------------------------------- #
def test_llm_cannot_self_promote_to_production():
    svc = _svc()
    cr = svc.propose_change(
        strategy_id="s1", parent_version=None,
        proposed_parameters={"momentum_window": 30, "max_asset_weight": 0.10},
        proposed_factor_refs=(), rationale="initial",
        actor_id="agent-buddy", actor_type="agent",
    )
    v = svc.validate_and_derive(
        request_id=cr.request_id, version_label="v1", feature_set_hash="fs",
        market=Market.CN, horizon_days=20, decided_by="ops",
    )
    # walk to CANARY
    svc.transition(strategy_id="s1", version="v1", to=StrategyState.VALIDATED,
                   decided_by="ops")
    svc.record_evaluation(strategy_id="s1", version="v1",
                          window_start="2023-01-01", window_end="2024-01-01",
                          regime_slices=("bull",), metrics={"ic": 0.12})
    svc.transition(strategy_id="s1", version="v1", to=StrategyState.BACKTESTED,
                   decided_by="ops")
    svc.transition(strategy_id="s1", version="v1", to=StrategyState.SHADOW,
                   decided_by="ops")
    svc.transition(strategy_id="s1", version="v1", to=StrategyState.CANARY,
                   decided_by="ops", gate_passed=True)

    # LLM attempts PRODUCTION without approval_id -> blocked
    with pytest.raises(UnapprovedPromotionError):
        svc.transition(strategy_id="s1", version="v1",
                       to=StrategyState.PRODUCTION,
                       decided_by="agent-buddy", actor_type="agent")

    # human with approval_id succeeds
    prod = svc.transition(strategy_id="s1", version="v1",
                          to=StrategyState.PRODUCTION,
                          decided_by="ops-human", approval_id="APR-001",
                          actor_type="human")
    assert prod.state is StrategyState.PRODUCTION
    assert prod.approval_id == "APR-001"


def test_service_rejects_transition_without_eval():
    svc = _svc()
    cr = svc.propose_change(
        strategy_id="s1", parent_version=None,
        proposed_parameters={"momentum_window": 30, "max_asset_weight": 0.10},
        proposed_factor_refs=(), rationale="initial", actor_id="ops",
    )
    svc.validate_and_derive(
        request_id=cr.request_id, version_label="v1", feature_set_hash="fs",
        market=Market.CN, horizon_days=20,
    )
    svc.transition(strategy_id="s1", version="v1", to=StrategyState.VALIDATED,
                   decided_by="ops")
    with pytest.raises(EvaluationMissingError):
        svc.transition(strategy_id="s1", version="v1",
                       to=StrategyState.BACKTESTED, decided_by="ops")


def test_service_rejects_proposal_without_rationale():
    svc = _svc()
    from packages.strategy_governance.errors import StrategyGovernanceError
    with pytest.raises(StrategyGovernanceError, match="rationale"):
        svc.propose_change(
            strategy_id="s1", parent_version=None,
            proposed_parameters={"momentum_window": 30},
            proposed_factor_refs=(), rationale="  ", actor_id="agent",
        )


def test_service_rejects_unknown_schema():
    # strategy with no declared schema -> everything rejected
    eng = _engine()
    svc = StrategyGovernanceService(
        versions=SqlStrategyVersionRepository(eng),
        change_requests=SqlChangeRequestRepository(eng),
        eval_runs=SqlEvaluationRunRepository(eng),
        decisions=SqlPromotionDecisionRepository(eng),
        audit=InMemoryAuditSink(),
        schemas={},  # no schema for any strategy
    )
    cr = svc.propose_change(
        strategy_id="unknown_strat", parent_version=None,
        proposed_parameters={"x": 1}, proposed_factor_refs=(),
        rationale="test", actor_id="ops",
    )
    from packages.strategy_governance.errors import StrategyGovernanceError
    with pytest.raises(StrategyGovernanceError, match="no schema"):
        svc.validate_and_derive(
            request_id=cr.request_id, version_label="v1", feature_set_hash="fs",
            market=Market.CN, horizon_days=20,
        )


# --------------------------------------------------------------------------- #
# 5. repository round-trip
# --------------------------------------------------------------------------- #
def test_repository_round_trip():
    eng = _engine()
    svr = SqlStrategyVersionRepository(eng)
    v = _v1("rt")
    svr.save(v)
    got = svr.get("rt", "v1")
    assert got is not None
    assert got.state is StrategyState.DRAFT
    assert got.parameter_set.values == {"momentum_window": 30, "max_asset_weight": 0.10}
    assert got.market is Market.CN
    assert got.factor_refs == ()


def test_repository_cas_state_change():
    eng = _engine()
    svr = SqlStrategyVersionRepository(eng)
    svr.save(_v1("cas"))
    assert svr.compare_and_set_state("cas", "v1", StrategyState.DRAFT,
                                     StrategyState.VALIDATED) is True
    got = svr.get("cas", "v1")
    assert got.state is StrategyState.VALIDATED


def test_repository_cas_rejects_stale_expected():
    eng = _engine()
    svr = SqlStrategyVersionRepository(eng)
    svr.save(_v1("cas2"))
    # move DRAFT -> VALIDATED
    svr.compare_and_set_state("cas2", "v1", StrategyState.DRAFT,
                              StrategyState.VALIDATED)
    # stale CAS expecting DRAFT again must fail (state is now VALIDATED)
    assert svr.compare_and_set_state("cas2", "v1", StrategyState.DRAFT,
                                     StrategyState.REJECTED) is False


# --------------------------------------------------------------------------- #
# 6. concurrent promotion — only one succeeds (§12)
# --------------------------------------------------------------------------- #
def test_concurrent_promotion_only_one_succeeds():
    """Two services race to promote the same version to PRODUCTION.
    validate_transition passes for both (same pre-state), but CAS makes only
    the first commit win; the second sees rowcount==0 and raises."""
    eng = _engine()
    # shared setup: walk a version up to CANARY
    svc = _svc(eng, "race")
    cr = svc.propose_change(
        strategy_id="race", parent_version=None,
        proposed_parameters={"momentum_window": 30, "max_asset_weight": 0.10},
        proposed_factor_refs=(), rationale="race", actor_id="ops",
    )
    svc.validate_and_derive(
        request_id=cr.request_id, version_label="v1", feature_set_hash="fs",
        market=Market.CN, horizon_days=20,
    )
    svc.transition(strategy_id="race", version="v1", to=StrategyState.VALIDATED,
                   decided_by="ops")
    svc.record_evaluation(strategy_id="race", version="v1",
                          window_start="2023-01-01", window_end="2024-01-01",
                          regime_slices=("bull",), metrics={"ic": 0.12})
    svc.transition(strategy_id="race", version="v1", to=StrategyState.BACKTESTED,
                   decided_by="ops")
    svc.transition(strategy_id="race", version="v1", to=StrategyState.SHADOW,
                   decided_by="ops")
    svc.transition(strategy_id="race", version="v1", to=StrategyState.CANARY,
                   decided_by="ops", gate_passed=True)

    # two independent services over the same engine race to PRODUCTION
    svc_a = _svc(eng, "race")
    svc_b = _svc(eng, "race")
    outcomes: list[str] = []
    # simulate serial race: A wins CAS, B's CAS finds state != CANARY
    try:
        svc_a.transition(strategy_id="race", version="v1",
                         to=StrategyState.PRODUCTION, decided_by="human-a",
                         approval_id="APR-A", actor_type="human")
        outcomes.append("a-won")
    except IllegalTransitionError:
        outcomes.append("a-lost")
    try:
        svc_b.transition(strategy_id="race", version="v1",
                         to=StrategyState.PRODUCTION, decided_by="human-b",
                         approval_id="APR-B", actor_type="human")
        outcomes.append("b-won")
    except IllegalTransitionError:
        outcomes.append("b-lost")

    assert "a-won" in outcomes
    assert "b-lost" in outcomes
    assert sum(1 for o in outcomes if o.endswith("-won")) == 1
    # exactly one PRODUCTION decision recorded
    decs = svc.list_decisions("race", "v1")
    prod_decs = [d for d in decs if d.to_state is StrategyState.PRODUCTION]
    assert len(prod_decs) == 1
    assert prod_decs[0].approval_id == "APR-A"


# --------------------------------------------------------------------------- #
# 7. rollback retires production
# --------------------------------------------------------------------------- #
def test_rollback_retires_production():
    eng = _engine()
    svc = _svc(eng, "rb")
    cr = svc.propose_change(
        strategy_id="rb", parent_version=None,
        proposed_parameters={"momentum_window": 30, "max_asset_weight": 0.10},
        proposed_factor_refs=(), rationale="rb", actor_id="ops",
    )
    svc.validate_and_derive(
        request_id=cr.request_id, version_label="v1", feature_set_hash="fs",
        market=Market.CN, horizon_days=20,
    )
    svc.transition(strategy_id="rb", version="v1", to=StrategyState.VALIDATED,
                   decided_by="ops")
    svc.record_evaluation(strategy_id="rb", version="v1",
                          window_start="2023-01-01", window_end="2024-01-01",
                          regime_slices=("bull",), metrics={"ic": 0.1})
    svc.transition(strategy_id="rb", version="v1", to=StrategyState.BACKTESTED,
                   decided_by="ops")
    svc.transition(strategy_id="rb", version="v1", to=StrategyState.SHADOW,
                   decided_by="ops")
    svc.transition(strategy_id="rb", version="v1", to=StrategyState.CANARY,
                   decided_by="ops", gate_passed=True)
    svc.transition(strategy_id="rb", version="v1", to=StrategyState.PRODUCTION,
                   decided_by="human", approval_id="APR-RB", actor_type="human")
    # rollback
    retired = svc.rollback(strategy_id="rb", decided_by="human",
                           approval_id="APR-ROLLBACK", reason="drift")
    assert retired.state is StrategyState.RETIRED
    # no production anymore
    assert svc.get_production("rb") is None
