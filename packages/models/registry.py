"""Model registry with strict state machine and per-market production uniqueness.

Cross-market share of PRODUCTION model is FORBIDDEN (spec §模型注册 顶层).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Protocol

from packages.common.errors import QuantError
from packages.common.instrument_id import Market
from packages.common.time_utils import utcnow


class ModelState(str, Enum):
    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    PRODUCTION = "PRODUCTION"
    RETIRED = "RETIRED"


class ModelTransitionError(QuantError):
    pass


_ALLOWED: dict[ModelState, set[ModelState]] = {
    ModelState.DRAFT:      {ModelState.CANDIDATE, ModelState.RETIRED},
    ModelState.CANDIDATE:  {ModelState.SHADOW, ModelState.PRODUCTION, ModelState.RETIRED},
    ModelState.SHADOW:     {ModelState.PRODUCTION, ModelState.CANDIDATE, ModelState.RETIRED},
    ModelState.PRODUCTION: {ModelState.RETIRED, ModelState.SHADOW},
    ModelState.RETIRED:    set(),  # terminal
}


@dataclass(frozen=True, slots=True)
class ModelRecord:
    model_id: str
    version: str
    market: Market
    horizon_days: int
    feature_set_hash: str
    state: ModelState
    created_at: datetime
    approved_by: str | None
    approval_id: str | None
    metrics: dict[str, float] = field(default_factory=dict)
    notes: str | None = None


def validate_transition(rec: ModelRecord, to: ModelState, *,
                        promotion_gate: "object | None" = None,
                        approval_id: str | None = None,
                        existing_production: ModelRecord | None = None) -> None:
    """Pure domain rule for model state transitions.

    Shared by InMemory and Sql registries so the SQL path cannot bypass the
    state machine, promotion gate, or approval requirements (issue #10
    Phase 1: "唯一状态变更入口"). Raises ModelTransitionError if illegal.
    """
    if to not in _ALLOWED[rec.state]:
        raise ModelTransitionError(f"illegal transition {rec.state} -> {to}")
    # §81.1: CANDIDATE cannot advance past baseline scrutiny.
    if rec.state is ModelState.CANDIDATE and to in {ModelState.SHADOW,
                                                    ModelState.PRODUCTION}:
        if promotion_gate is None:
            raise ModelTransitionError(
                "CANDIDATE -> SHADOW/PRODUCTION requires promotion_gate "
                "evidence (§81.1: must beat all declared baselines)"
            )
        passed = getattr(promotion_gate, "passed", None)
        if not passed:
            raise ModelTransitionError(
                f"promotion_gate rejects candidate {rec.model_id}@{rec.version}: "
                f"{getattr(promotion_gate, 'losses', ())}"
            )
    # Guard: per-model_id PRODUCTION uniqueness — a model_id may have only one
    # PRODUCTION version at a time. Different model_ids may coexist under the
    # same (market, horizon) (e.g. CN A-share vs CN fund-NAV). The caller
    # passes the model_id's current PRODUCTION record as existing_production.
    if to is ModelState.PRODUCTION:
        if existing_production is not None \
                and existing_production.model_id == rec.model_id \
                and existing_production.version != rec.version:
            raise ModelTransitionError(
                f"model {rec.model_id} already has PRODUCTION version "
                f"{existing_production.version} (market {rec.market} horizon {rec.horizon_days})"
            )
        if not approval_id:
            raise ModelTransitionError("PRODUCTION promotion requires approval_id")


class ModelRegistry(Protocol):
    def register(self, rec: ModelRecord) -> None: ...
    def transition(self, model_id: str, version: str, to: ModelState,
                   *, actor: str, approval_id: str | None = None) -> ModelRecord: ...
    def get_production(self, market: Market, horizon_days: int) -> ModelRecord | None: ...


@dataclass
class InMemoryModelRegistry:
    _by_key: dict[tuple[str, str], ModelRecord] = field(default_factory=dict)
    _artifacts: dict[tuple[str, str], object] = field(default_factory=dict)

    def register(self, rec: ModelRecord, *, artifact: object | None = None) -> None:
        """Register metadata; optionally attach the executable model artifact.

        The artifact conforms to ``packages.models.base.Model``. Storing it
        beside the record lets the inference service look up the callable by
        the same (model_id, version) key used by governance/audit.
        """
        key = (rec.model_id, rec.version)
        if key in self._by_key:
            raise ModelTransitionError(f"model {rec.model_id}@{rec.version} already registered")
        if rec.state is not ModelState.DRAFT:
            raise ModelTransitionError("new models must start in DRAFT")
        self._by_key[key] = rec
        if artifact is not None:
            self._artifacts[key] = artifact

    def attach_artifact(self, model_id: str, version: str, artifact: object) -> None:
        key = (model_id, version)
        if key not in self._by_key:
            raise ModelTransitionError(f"unknown model {model_id}@{version}")
        self._artifacts[key] = artifact

    def get_artifact(self, model_id: str, version: str) -> object | None:
        return self._artifacts.get((model_id, version))

    def transition(
        self, model_id: str, version: str, to: ModelState,
        *, actor: str, approval_id: str | None = None,
        promotion_gate: "object | None" = None,
    ) -> ModelRecord:
        key = (model_id, version)
        rec = self._by_key.get(key)
        if rec is None:
            raise ModelTransitionError(f"unknown model {model_id}@{version}")
        if to not in _ALLOWED[rec.state]:
            raise ModelTransitionError(f"illegal transition {rec.state} -> {to}")

        existing = (next((r for r in self._by_key.values()
                          if r.state is ModelState.PRODUCTION
                          and r.model_id == model_id
                          and r.version != version), None)
                    if to is ModelState.PRODUCTION else None)
        validate_transition(rec, to, promotion_gate=promotion_gate,
                            approval_id=approval_id,
                            existing_production=existing)

        new = replace(rec, state=to,
                      approved_by=actor if to is ModelState.PRODUCTION else rec.approved_by,
                      approval_id=approval_id or rec.approval_id)
        self._by_key[key] = new
        return new

    def get_production(self, market: Market, horizon_days: int) -> ModelRecord | None:
        for rec in self._by_key.values():
            if rec.state is ModelState.PRODUCTION and rec.market is market \
                    and rec.horizon_days == horizon_days:
                return rec
        return None
