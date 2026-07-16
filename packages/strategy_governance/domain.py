"""Immutable strategy-governance entities and lifecycle states.

All entities are frozen dataclasses — a version is never mutated in place; a
new version is derived from a parent via ``StrategyVersion.derive(...)``.

Lifecycle (issue #10 §3):

    DRAFT -> VALIDATED -> BACKTESTED -> SHADOW -> CANARY -> PRODUCTION
      \\-> REJECTED                        \\-> SUSPENDED / RETIRED

Every forward transition past VALIDATED requires an EvaluationRun; CANARY ->
PRODUCTION additionally requires a human approval_id.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from packages.common.instrument_id import Market
from packages.common.time_utils import utcnow


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
class StrategyState(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    BACKTESTED = "BACKTESTED"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    PRODUCTION = "PRODUCTION"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


# States that may hold the single "live" production slot.
PRODUCTION_ELIGIBLE = frozenset({StrategyState.PRODUCTION})

# Terminal states — no further transitions out.
TERMINAL = frozenset({StrategyState.REJECTED, StrategyState.RETIRED})


class ChangeRequestStatus(str, Enum):
    PROPOSED = "PROPOSED"      # LLM/human filed it
    VALIDATED = "VALIDATED"    # params passed schema, version derived
    REJECTED = "REJECTED"      # schema/validation failed, or human declined
    SUPERSEDED = "SUPERSEDED"  # a newer request replaced it


class EvaluationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PromotionOutcome(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_REJECTED = "AUTO_REJECTED"  # gate failure, no human needed


# --------------------------------------------------------------------------- #
# Parameter set
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ParameterSetVersion:
    """An immutable, content-hashed parameter set bound to a strategy version.

    ``values`` must pass the declared schema before a StrategyVersion can move
    from DRAFT to VALIDATED (see policy.validate_parameter_schema).
    """

    values: dict[str, Any]
    schema_version: str
    content_hash: str          # sha256 of canonical(values)
    created_by: str
    created_at: datetime = field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Strategy version
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StrategyVersion:
    """An immutable strategy version. Derived, never edited."""

    strategy_id: str           # e.g. "cn_equity_reg20d"
    version: str               # semver-ish, e.g. "v1"
    parent_version: str | None # None only for the first version
    market: Market
    horizon_days: int
    state: StrategyState
    parameter_set: ParameterSetVersion
    feature_set_hash: str
    factor_refs: tuple[str, ...] = ()   # FactorVersion ids, immutable
    model_ref: str | None = None        # model_id@version once trained
    code_commit: str | None = None
    config_hash: str | None = None
    created_by: str = "system"
    created_at: datetime = field(default_factory=utcnow)
    approved_by: str | None = None
    approval_id: str | None = None

    def derive(
        self,
        *,
        version: str,
        parameter_set: ParameterSetVersion,
        feature_set_hash: str | None = None,
        factor_refs: tuple[str, ...] | None = None,
        model_ref: str | None = None,
        code_commit: str | None = None,
        config_hash: str | None = None,
        created_by: str = "system",
    ) -> "StrategyVersion":
        """Create a new DRAFT version derived from this one."""
        return StrategyVersion(
            strategy_id=self.strategy_id,
            version=version,
            parent_version=self.version,
            market=self.market,
            horizon_days=self.horizon_days,
            state=StrategyState.DRAFT,
            parameter_set=parameter_set,
            feature_set_hash=feature_set_hash or self.feature_set_hash,
            factor_refs=factor_refs if factor_refs is not None else self.factor_refs,
            model_ref=model_ref,
            code_commit=code_commit,
            config_hash=config_hash,
            created_by=created_by,
        )

    def with_state(self, state: StrategyState, **changes: Any) -> "StrategyVersion":
        """Return a copy with a new state. Used only by the policy/service layer
        after validate_transition has approved the move."""
        return replace(self, state=state, **changes)


# --------------------------------------------------------------------------- #
# Change request
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ChangeRequest:
    """A filed change proposal — the ONLY thing an LLM is allowed to create.

    Filed PROPOSED; a human/service validates parameters, derives a
    StrategyVersion, then drives the lifecycle forward.
    """

    request_id: str
    strategy_id: str
    parent_version: str | None
    proposed_parameters: dict[str, Any]
    proposed_factor_refs: tuple[str, ...]
    rationale: str             # LLM must state the hypothesis
    status: ChangeRequestStatus
    created_by: str            # actor_id (agent/human)
    created_at: datetime = field(default_factory=utcnow)
    derived_version: str | None = None   # set once a StrategyVersion is derived
    decided_by: str | None = None
    decided_at: datetime | None = None
    rejection_reason: str | None = None


# --------------------------------------------------------------------------- #
# Evaluation run
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """A deterministic, reproducible evaluation of a StrategyVersion."""

    run_id: str
    strategy_id: str
    version: str
    status: EvaluationStatus
    window_start: str          # ISO date
    window_end: str
    regime_slices: tuple[str, ...]   # e.g. ("bull_2023", "bear_2022")
    metrics: dict[str, float] = field(default_factory=dict)
    started_by: str = "system"
    started_at: datetime = field(default_factory=utcnow)
    completed_at: datetime | None = None
    repro_hash: str | None = None       # hash of frozen data + code + params
    failure_reason: str | None = None


# --------------------------------------------------------------------------- #
# Promotion decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """A recorded promotion/rejection decision for an audit trail."""

    decision_id: str
    strategy_id: str
    version: str
    from_state: StrategyState
    to_state: StrategyState
    outcome: PromotionOutcome
    evaluation_run_id: str | None
    decided_by: str           # human actor_id for APPROVED; service for AUTO_REJECTED
    approval_id: str | None   # required when to_state == PRODUCTION
    decided_at: datetime = field(default_factory=utcnow)
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Factor governance (Phase 4, issue #10 §11)
# --------------------------------------------------------------------------- #
class FactorState(str, Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class FactorVersion:
    """An immutable factor version with point-in-time availability.

    ``available_from`` is the date from which this factor's data exists and is
    consumable. Using the factor at any ``as_of < available_from`` is a
    future-leak and is rejected by policy.validate_factor_availability.

    ``dependencies`` lists factor_ids that must themselves be available at
    ``as_of`` for this factor to be usable (e.g. a composite factor built on
    raw returns depends on the raw-return factor).
    """

    factor_id: str
    version: str
    definition_hash: str          # hash of the factor's definition (immutable)
    available_from: date          # PIT: factor data exists from this date
    dependencies: tuple[str, ...] = ()
    description: str = ""
    state: FactorState = FactorState.ACTIVE
    created_by: str = "system"
    created_at: datetime = field(default_factory=utcnow)
