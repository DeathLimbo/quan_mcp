"""Pure governance rules — no I/O, no mutation, no side effects.

The single source of truth for *whether* a transition is legal. The service
layer calls these and is forbidden from inlining its own rules.
"""
from __future__ import annotations

from typing import Any

from packages.strategy_governance.domain import (
    EvaluationRun,
    EvaluationStatus,
    ParameterSetVersion,
    StrategyState,
    StrategyVersion,
)
from packages.strategy_governance.errors import (
    EvaluationMissingError,
    IllegalTransitionError,
    SchemaValidationError,
    UnapprovedPromotionError,
)

# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #
# Legal forward + safety transitions (issue #10 §3).
_ALLOWED: dict[StrategyState, frozenset[StrategyState]] = {
    StrategyState.DRAFT: frozenset({StrategyState.VALIDATED, StrategyState.REJECTED}),
    StrategyState.VALIDATED: frozenset({StrategyState.BACKTESTED, StrategyState.REJECTED}),
    StrategyState.BACKTESTED: frozenset({StrategyState.SHADOW, StrategyState.REJECTED}),
    StrategyState.SHADOW: frozenset({
        StrategyState.CANARY, StrategyState.SUSPENDED, StrategyState.RETIRED,
    }),
    StrategyState.CANARY: frozenset({
        StrategyState.PRODUCTION, StrategyState.SUSPENDED, StrategyState.RETIRED,
    }),
    StrategyState.PRODUCTION: frozenset({StrategyState.SUSPENDED, StrategyState.RETIRED}),
    StrategyState.SUSPENDED: frozenset({StrategyState.RETIRED}),
    StrategyState.REJECTED: frozenset(),   # terminal
    StrategyState.RETIRED: frozenset(),    # terminal
}

# Transitions that require a COMPLETED EvaluationRun to exist.
_REQUIRES_EVAL = frozenset({StrategyState.BACKTESTED, StrategyState.SHADOW})

# Transitions that require a non-empty human approval_id.
_REQUIRES_APPROVAL = frozenset({StrategyState.PRODUCTION})

# Transitions that require a passing shadow/canary gate signal.
_REQUIRES_GATE = frozenset({StrategyState.CANARY})


def validate_transition(
    version: StrategyVersion,
    to: StrategyState,
    *,
    evaluation_runs: list[EvaluationRun] | None = None,
    approval_id: str | None = None,
    gate_passed: bool | None = None,
) -> None:
    """Raise if ``version -> to`` is not a legal, gated transition.

    Rules:
      * ``to`` must be in the allowed set for ``version.state``.
      * BACKTESTED / SHADOW require at least one COMPLETED EvaluationRun for
        this strategy version.
      * CANARY requires ``gate_passed is True`` (shadow window clean).
      * PRODUCTION requires a non-empty ``approval_id`` (human) — an LLM or
        script passing ``approval_id=None`` is rejected.
    """
    if to not in _ALLOWED.get(version.state, frozenset()):
        raise IllegalTransitionError(
            f"illegal transition {version.strategy_id}@{version.version} "
            f"{version.state.value} -> {to.value}"
        )

    if to in _REQUIRES_EVAL:
        runs = evaluation_runs or []
        completed = [
            r for r in runs
            if r.strategy_id == version.strategy_id
            and r.version == version.version
            and r.status == EvaluationStatus.COMPLETED
        ]
        if not completed:
            raise EvaluationMissingError(
                f"{version.strategy_id}@{version.version} -> {to.value} requires "
                f"a COMPLETED EvaluationRun (have {len(runs)} run(s), 0 completed)"
            )

    if to in _REQUIRES_GATE and gate_passed is not True:
        raise IllegalTransitionError(
            f"{version.strategy_id}@{version.version} -> CANARY requires "
            f"a passing shadow gate (gate_passed={gate_passed!r})"
        )

    if to in _REQUIRES_APPROVAL:
        if not approval_id or not approval_id.strip():
            raise UnapprovedPromotionError(
                f"{version.strategy_id}@{version.version} -> PRODUCTION requires "
                f"a non-empty human approval_id (got {approval_id!r})"
            )


# --------------------------------------------------------------------------- #
# Parameter whitelist schema
# --------------------------------------------------------------------------- #
class ParamSpec:
    """Declarative spec for one whitelisted parameter.

    ``type`` is one of ``integer``/``number``/``string``/``boolean``.
    ``minimum``/``maximum`` apply to numeric types. ``required`` defaults True.
    """

    __slots__ = ("type", "minimum", "maximum", "required", "choices")

    def __init__(
        self,
        *,
        type: str,
        minimum: float | int | None = None,
        maximum: float | int | None = None,
        required: bool = True,
        choices: tuple[Any, ...] | None = None,
    ) -> None:
        self.type = type
        self.minimum = minimum
        self.maximum = maximum
        self.required = required
        self.choices = choices


_TYPE_MAP = {
    "integer": int,
    "number": (int, float),
    "string": str,
    "boolean": bool,
}


def validate_parameter_schema(
    values: dict[str, Any],
    schema: dict[str, ParamSpec],
) -> None:
    """Validate ``values`` against a whitelist ``schema``.

    Raises SchemaValidationError if:
      * a declared required param is missing;
      * a value's type does not match its spec;
      * a numeric value is outside [minimum, maximum];
      * a value is not in ``choices``;
      * ``values`` contains a key NOT declared in ``schema`` (whitelist —
        undeclared params are forbidden, preventing smuggled tunings).
    """
    # 1. undeclared keys are forbidden
    extra = set(values) - set(schema)
    if extra:
        raise SchemaValidationError(
            f"undeclared parameters rejected by whitelist: {sorted(extra)}"
        )

    for key, spec in schema.items():
        if key not in values:
            if spec.required:
                raise SchemaValidationError(f"missing required parameter: {key!r}")
            continue
        v = values[key]
        expected = _TYPE_MAP.get(spec.type)
        if expected is None:
            raise SchemaValidationError(f"unknown schema type {spec.type!r} for {key!r}")
        # NOTE: bool is a subclass of int in Python; reject bool when integer expected.
        if spec.type == "integer" and isinstance(v, bool):
            raise SchemaValidationError(f"{key!r} must be integer, got bool")
        if not isinstance(v, expected):
            raise SchemaValidationError(
                f"{key!r} must be {spec.type}, got {type(v).__name__}"
            )
        if spec.choices is not None and v not in spec.choices:
            raise SchemaValidationError(
                f"{key!r}={v!r} not in choices {spec.choices}"
            )
        if spec.minimum is not None and v < spec.minimum:
            raise SchemaValidationError(
                f"{key!r}={v!r} below minimum {spec.minimum}"
            )
        if spec.maximum is not None and v > spec.maximum:
            raise SchemaValidationError(
                f"{key!r}={v!r} above maximum {spec.maximum}"
            )


# --------------------------------------------------------------------------- #
# Readable change diff
# --------------------------------------------------------------------------- #
def compute_change_diff(
    parent: ParameterSetVersion | None,
    child: ParameterSetVersion,
) -> list[str]:
    """Human-readable diff lines between a parent and child parameter set.

    Returns lines like:
      + momentum_window: 20 -> 30
      ~ max_asset_weight: 0.10 -> 0.12
      - removed_param dropped
      + new_param=0.05 added
    An empty parent (first version) yields one ``+ initial`` line per key.
    """
    p = parent.values if parent is not None else {}
    c = child.values
    lines: list[str] = []
    for key in sorted(set(p) | set(c)):
        if key in p and key not in c:
            lines.append(f"- {key} dropped (was {p[key]!r})")
        elif key not in p and key in c:
            lines.append(f"+ {key}={c[key]!r} added")
        elif p[key] != c[key]:
            lines.append(f"~ {key}: {p[key]!r} -> {c[key]!r}")
    if not lines:
        lines.append("(no parameter changes)")
    return lines
