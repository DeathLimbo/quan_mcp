"""Stable error types for the strategy-governance domain.

These are part of the public API surface — callers (MCP/API/tests) depend on
their names and messages, so they must not be renamed silently.
"""
from __future__ import annotations


class StrategyGovernanceError(Exception):
    """Base for all strategy-governance domain errors."""


class IllegalTransitionError(StrategyGovernanceError):
    """A state transition violates the immutable lifecycle state machine."""


class SchemaValidationError(StrategyGovernanceError):
    """A parameter set fails the declared whitelist schema."""


class UnapprovedPromotionError(StrategyGovernanceError):
    """A promotion to PRODUCTION/CANARY was attempted without human approval."""


class VersionExistsError(StrategyGovernanceError):
    """A strategy version with the same content_hash already exists (immutable)."""


class ImmutableVersionError(StrategyGovernanceError):
    """An attempt was made to mutate an already-created version in place."""


class EvaluationMissingError(StrategyGovernanceError):
    """A promotion gate requires an EvaluationRun that does not exist."""
