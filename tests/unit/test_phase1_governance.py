"""Phase 1 governance tests (issue #10): the state machine cannot be bypassed.

Covers the "唯一状态变更入口" constraint — validate_transition is the single
authority for state transitions, and SqlModelRegistry.transition goes through it
(+ compare-and-set optimistic lock) so the SQL path can no longer skip the state
machine, promotion gate, or approval requirements.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import Market
from packages.common.time_utils import utcnow
from packages.models.registry import (
    ModelRecord, ModelState, ModelTransitionError, validate_transition,
)
from packages.persistence.repositories import (
    SqlModelRegistry, _metadata, model_registry_t,
)


def _rec(model_id="m1", version="v1", state=ModelState.DRAFT, market=Market.US):
    return ModelRecord(
        model_id=model_id, version=version, market=market, horizon_days=20,
        feature_set_hash="abc", state=state, created_at=utcnow(),
        approved_by=None, approval_id=None, metrics={}, notes=None,
    )


def _gate():
    return SimpleNamespace(passed=True, losses=())


def _engine():
    eng = sa.create_engine("sqlite://")
    _metadata.create_all(eng, tables=[model_registry_t])
    return eng


# ---- pure domain rule: validate_transition -------------------------------

def test_validate_rejects_draft_to_production_directly():
    """DRAFT→PRODUCTION is illegal (must go DRAFT→CANDIDATE→PRODUCTION)."""
    with pytest.raises(ModelTransitionError, match="illegal transition"):
        validate_transition(_rec(state=ModelState.DRAFT), ModelState.PRODUCTION,
                            approval_id="apr")


def test_validate_rejects_candidate_to_production_without_gate():
    """CANDIDATE→PRODUCTION requires promotion_gate evidence (§81.1)."""
    with pytest.raises(ModelTransitionError, match="promotion_gate"):
        validate_transition(_rec(state=ModelState.CANDIDATE), ModelState.PRODUCTION,
                            approval_id="apr")


def test_validate_rejects_candidate_to_production_with_failing_gate():
    gate = SimpleNamespace(passed=False, losses=("buy_and_hold",))
    with pytest.raises(ModelTransitionError, match="promotion_gate rejects"):
        validate_transition(_rec(state=ModelState.CANDIDATE), ModelState.PRODUCTION,
                            promotion_gate=gate, approval_id="apr")


def test_validate_rejects_production_without_approval():
    """PRODUCTION promotion requires an approval_id (human sign-off)."""
    with pytest.raises(ModelTransitionError, match="approval_id"):
        validate_transition(_rec(state=ModelState.CANDIDATE), ModelState.PRODUCTION,
                            promotion_gate=_gate(), approval_id=None)


def test_validate_rejects_same_model_id_second_production():
    """Per-model_id uniqueness: a model_id may have only one PRODUCTION version."""
    existing = _rec(model_id="m1", version="v1", state=ModelState.PRODUCTION)
    with pytest.raises(ModelTransitionError, match="already has PRODUCTION"):
        validate_transition(
            _rec(model_id="m1", version="v2", state=ModelState.CANDIDATE),
            ModelState.PRODUCTION, promotion_gate=_gate(),
            approval_id="apr", existing_production=existing,
        )


def test_validate_allows_different_model_ids_same_market():
    """Different model_ids may coexist under same (market, horizon)."""
    existing = _rec(model_id="m1", version="v1", state=ModelState.PRODUCTION)
    # m2@v1 promoting — existing is m1, different model_id → allowed (no raise)
    validate_transition(
        _rec(model_id="m2", version="v1", state=ModelState.CANDIDATE),
        ModelState.PRODUCTION, promotion_gate=_gate(),
        approval_id="apr", existing_production=existing,
    )


# ---- SqlModelRegistry cannot bypass the state machine --------------------

def test_sql_registry_rejects_draft_to_production_directly(tmp_path):
    """The SQL path must not allow DRAFT→PRODUCTION even with approval."""
    reg = SqlModelRegistry(_engine(), str(tmp_path))
    reg.register(_rec())
    with pytest.raises(ModelTransitionError, match="illegal transition"):
        reg.transition("m1", "v1", ModelState.PRODUCTION, actor="x", approval_id="apr")


def test_sql_registry_rejects_production_without_approval(tmp_path):
    reg = SqlModelRegistry(_engine(), str(tmp_path))
    reg.register(_rec())
    reg.transition("m1", "v1", ModelState.CANDIDATE, actor="x")
    with pytest.raises(ModelTransitionError, match="approval_id"):
        reg.transition("m1", "v1", ModelState.PRODUCTION, actor="x",
                       promotion_gate=_gate(), approval_id=None)


def test_sql_registry_legal_transition_updates_state_and_approval(tmp_path):
    """Normal legal path DRAFT→CANDIDATE→PRODUCTION: compare-and-set succeeds,
    state updates to PRODUCTION, approval_id and approved_by recorded.

    (True concurrent CAS contention needs multi-threading — covered by an
    integration test in Phase 2. The compare_and_set WHERE clause +
    rowcount==0 guard is verified by code review here.)"""
    eng = _engine()
    reg = SqlModelRegistry(eng, str(tmp_path))
    reg.register(_rec())
    reg.transition("m1", "v1", ModelState.CANDIDATE, actor="x")
    reg.transition("m1", "v1", ModelState.PRODUCTION, actor="x",
                   approval_id="apr1", promotion_gate=_gate())
    prod = reg.get_production(Market.US, 20)
    assert prod is not None
    assert prod.state is ModelState.PRODUCTION
    assert prod.approval_id == "apr1"
    assert prod.approved_by == "x"
