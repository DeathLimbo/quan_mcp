"""§81.1 promotion-gate tests.

The gate refuses CANDIDATE -> SHADOW / PRODUCTION unless the candidate model
strictly beats every declared baseline on every gate metric key (default:
Spearman IC + net return on the same holdout).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from packages.common.instrument_id import Market
from packages.evaluation.promotion import (
    DEFAULT_GATE_KEYS, PromotionGateFailed, beats_all_baselines,
    require_beats_all_baselines,
)
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState, ModelTransitionError,
)


# ---------------------------------------------------------------------------
# Pure gate function.
# ---------------------------------------------------------------------------
def test_gate_default_keys_are_ic_and_net_return():
    assert DEFAULT_GATE_KEYS == ("ic", "net_return")


def test_gate_passes_when_candidate_strictly_beats_every_baseline():
    result = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={
            "BuyAndHold":  {"ic": 0.02, "net_return": 0.03},
            "FixedDCA":    {"ic": 0.01, "net_return": 0.04},
        },
    )
    assert result.passed is True
    assert result.losses == ()
    assert result.keys_checked == ("ic", "net_return")


def test_gate_fails_when_candidate_ties_on_any_metric():
    """§81.1 requires STRICT improvement; equal metrics fail."""
    result = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.05, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.05, "net_return": 0.03}},
    )
    assert result.passed is False
    assert len(result.losses) == 1
    b, k, c, v = result.losses[0]
    assert (b, k) == ("BuyAndHold", "ic")
    assert c == v == 0.05


def test_gate_fails_when_candidate_loses_to_any_single_baseline():
    """Even if candidate beats 4/5 baselines, one loss fails the gate."""
    result = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={
            "BuyAndHold":     {"ic": 0.02, "net_return": 0.03},
            "MovingAverage":  {"ic": 0.03, "net_return": 0.04},
            "XSMomentum":     {"ic": 0.15, "net_return": 0.05},  # beats candidate on ic
            "FixedDCA":       {"ic": 0.01, "net_return": 0.02},
        },
    )
    assert result.passed is False
    losses_by_baseline = {b: (k, c, v) for b, k, c, v in result.losses}
    assert "XSMomentum" in losses_by_baseline
    assert losses_by_baseline["XSMomentum"][0] == "ic"


def test_gate_missing_metric_counts_as_loss():
    result = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10},   # missing net_return
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    assert result.passed is False
    # Two entries expected: candidate wins ic, loses on missing net_return.
    metrics_that_lost = {k for _, k, _, _ in result.losses}
    assert "net_return" in metrics_that_lost


def test_require_beats_all_baselines_raises_on_failure():
    with pytest.raises(PromotionGateFailed, match="did not beat baselines"):
        require_beats_all_baselines(
            candidate_id="cand@v1",
            candidate_metrics={"ic": 0.01, "net_return": 0.02},
            baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
        )


def test_require_beats_all_baselines_returns_result_on_success():
    result = require_beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    assert result.passed is True


def test_gate_result_serialization_round_trip():
    r = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    d = r.as_dict()
    assert d["passed"] is True
    assert d["candidate_id"] == "cand@v1"
    assert d["losses"] == []
    assert set(d["keys_checked"]) == {"ic", "net_return"}


# ---------------------------------------------------------------------------
# Registry integration.
# ---------------------------------------------------------------------------
def _fresh_registry(*, model_id: str = "cand", version: str = "v1"):
    reg = InMemoryModelRegistry()
    rec = ModelRecord(
        model_id=model_id, version=version, market=Market.US, horizon_days=5,
        feature_set_hash="abc", state=ModelState.DRAFT,
        created_at=datetime.now(timezone.utc), approved_by=None,
        approval_id=None,
    )
    reg.register(rec)
    reg.transition(model_id, version, ModelState.CANDIDATE, actor="ops")
    return reg


def test_registry_rejects_candidate_to_shadow_without_gate():
    reg = _fresh_registry()
    with pytest.raises(ModelTransitionError, match="requires promotion_gate"):
        reg.transition("cand", "v1", ModelState.SHADOW, actor="ops")


def test_registry_rejects_candidate_to_production_without_gate():
    reg = _fresh_registry()
    with pytest.raises(ModelTransitionError, match="requires promotion_gate"):
        reg.transition("cand", "v1", ModelState.PRODUCTION,
                       actor="ops", approval_id="APR-1")


def test_registry_rejects_candidate_to_shadow_with_failing_gate():
    reg = _fresh_registry()
    gate = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.01, "net_return": 0.02},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    with pytest.raises(ModelTransitionError, match="promotion_gate rejects"):
        reg.transition("cand", "v1", ModelState.SHADOW, actor="ops",
                       promotion_gate=gate)


def test_registry_accepts_candidate_to_shadow_with_passing_gate():
    reg = _fresh_registry()
    gate = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    rec = reg.transition("cand", "v1", ModelState.SHADOW, actor="ops",
                          promotion_gate=gate)
    assert rec.state is ModelState.SHADOW


def test_registry_shadow_to_production_does_not_require_gate():
    """SHADOW->PRODUCTION carries prior gate evidence via SHADOW runtime; the
    strict gate check runs only at the CANDIDATE frontier."""
    reg = _fresh_registry()
    passing_gate = beats_all_baselines(
        candidate_id="cand@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    reg.transition("cand", "v1", ModelState.SHADOW, actor="ops",
                    promotion_gate=passing_gate)
    rec = reg.transition("cand", "v1", ModelState.PRODUCTION,
                          actor="ops", approval_id="APR-1")
    assert rec.state is ModelState.PRODUCTION
