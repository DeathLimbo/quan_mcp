"""Strategy governance domain — versioned, immutable strategy lifecycle.

Issue #10 Phase 2. Builds an auditable strategy-governance domain on top of the
existing compute (training/eval/drift). Entities are immutable: a version never
mutates in place, a new version is always derived from a parent.
"""
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
    EvaluationMissingError,
    IllegalTransitionError,
    ImmutableVersionError,
    SchemaValidationError,
    StrategyGovernanceError,
    UnapprovedPromotionError,
    VersionExistsError,
)
from packages.strategy_governance.policy import (
    ParamSpec,
    compute_change_diff,
    validate_parameter_schema,
    validate_transition,
)

__all__ = [
    "ChangeRequest",
    "ChangeRequestStatus",
    "EvaluationRun",
    "EvaluationStatus",
    "EvaluationMissingError",
    "IllegalTransitionError",
    "ImmutableVersionError",
    "ParameterSetVersion",
    "ParamSpec",
    "PromotionDecision",
    "PromotionOutcome",
    "SchemaValidationError",
    "StrategyGovernanceError",
    "StrategyState",
    "StrategyVersion",
    "UnapprovedPromotionError",
    "VersionExistsError",
    "compute_change_diff",
    "validate_parameter_schema",
    "validate_transition",
]
