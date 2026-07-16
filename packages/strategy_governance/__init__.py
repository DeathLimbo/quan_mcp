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
    WalkForwardFold,
    classify_regime,
)
from packages.strategy_governance.errors import (
    EvaluationMissingError,
    FactorDependencyError,
    FactorLeakageError,
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
    filter_available_factors,
    validate_factor_availability,
    validate_factor_dependencies,
    validate_parameter_schema,
    validate_transition,
)
from packages.strategy_governance.shadow import (
    DriftAutoSuspendConfig,
    DriftAutoSuspender,
    ShadowOutcome,
    ShadowPrediction,
    ShadowTracker,
)

__all__ = [
    "ChangeRequest",
    "ChangeRequestStatus",
    "DriftAutoSuspendConfig",
    "DriftAutoSuspender",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationStatus",
    "EvaluationMissingError",
    "FactorDependencyError",
    "FactorLeakageError",
    "FactorState",
    "FactorVersion",
    "IllegalTransitionError",
    "ImmutableVersionError",
    "ParameterSetVersion",
    "ParamSpec",
    "PromotionDecision",
    "PromotionOutcome",
    "SchemaValidationError",
    "ShadowOutcome",
    "ShadowPrediction",
    "ShadowTracker",
    "StrategyEvaluator",
    "StrategyGovernanceError",
    "StrategyState",
    "StrategyVersion",
    "UnapprovedPromotionError",
    "VersionExistsError",
    "WalkForwardFold",
    "classify_regime",
    "compute_change_diff",
    "filter_available_factors",
    "validate_factor_availability",
    "validate_factor_dependencies",
    "validate_parameter_schema",
    "validate_transition",
]
