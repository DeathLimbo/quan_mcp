"""RiskProposal — spec §86.

The 8-layer risk engine returns a *trace*. Downstream systems (portfolio,
paper-order, admin API) want a single verdict with an actionable weight and
a stable policy version tag for auditing.

``propose`` wraps the engine trace into a :class:`RiskProposal` that says
- ``APPROVED``  — take proposed_weight
- ``ADJUSTED``  — trim to the largest weight that would pass the engine
                  (bisected within a bounded number of steps)
- ``REJECTED``  — set approved_weight = 0, include all reject reasons
plus a global ``SAFE_MODE`` bit that force-collapses APPROVED to REJECTED
when the operator has flipped the kill switch or drift is HALT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from packages.risk.engine import (
    RiskContext, RiskDecision, RiskEngine, RiskVerdict, default_engine,
)


class ProposalStatus(str, Enum):
    APPROVED = "APPROVED"
    ADJUSTED = "ADJUSTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class RiskProposal:
    status: ProposalStatus
    approved_weight: float
    reasons: tuple[str, ...]
    policy_version: str
    trace: tuple[RiskDecision, ...]
    safe_mode: bool = False

    def is_tradeable(self) -> bool:
        return self.status is not ProposalStatus.REJECTED and self.approved_weight > 0.0


def _reasons_from(trace: tuple[RiskDecision, ...]) -> tuple[str, ...]:
    return tuple(
        f"{d.layer}:{d.code}:{d.reason}"
        for d in trace
        if d.verdict is not RiskVerdict.ACCEPT
    )


def propose(
    ctx: RiskContext,
    *,
    proposed_weight: float,
    engine: RiskEngine | None = None,
    policy_version: str = "risk_policy_v1",
    safe_mode: bool = False,
    max_adjust_iters: int = 6,
    build_ctx: Callable[[float], RiskContext] | None = None,
) -> RiskProposal:
    """Convert an engine trace into a single ``RiskProposal``.

    ``build_ctx`` allows the caller to re-run the engine at a smaller weight
    (needed for ADJUSTED status). If not given, an ADJUSTED verdict is only
    reached when a single re-run at half weight passes.
    """
    if proposed_weight < 0:
        raise ValueError("proposed_weight must be non-negative")
    engine = engine or default_engine()

    if safe_mode:
        trace = tuple(engine.evaluate(ctx))
        return RiskProposal(
            status=ProposalStatus.REJECTED,
            approved_weight=0.0,
            reasons=("global:SAFE_MODE:kill-switch engaged",),
            policy_version=policy_version,
            trace=trace,
            safe_mode=True,
        )

    trace = tuple(engine.evaluate(ctx))
    verdict = engine.final_verdict(trace)

    if verdict is RiskVerdict.ACCEPT:
        return RiskProposal(
            status=ProposalStatus.APPROVED,
            approved_weight=proposed_weight,
            reasons=(),
            policy_version=policy_version,
            trace=trace,
        )

    if verdict is RiskVerdict.REJECT:
        # Try to trim if caller supplied a builder AND the reject was a soft
        # limit (exposure). Hard rejects (permission, halted, delisted) never
        # get adjusted.
        reject = next(d for d in trace if d.verdict is RiskVerdict.REJECT)
        soft_layers = {"exposure_limits", "stress_delta"}
        if build_ctx is not None and reject.layer in soft_layers and proposed_weight > 0:
            lo, hi = 0.0, proposed_weight
            adjusted: float | None = None
            adj_trace = trace
            for _ in range(max_adjust_iters):
                mid = (lo + hi) / 2.0
                sub_trace = tuple(engine.evaluate(build_ctx(mid)))
                if engine.final_verdict(sub_trace) is RiskVerdict.ACCEPT:
                    adjusted = mid
                    adj_trace = sub_trace
                    lo = mid  # try larger
                else:
                    hi = mid
            if adjusted is not None and adjusted > 0:
                return RiskProposal(
                    status=ProposalStatus.ADJUSTED,
                    approved_weight=adjusted,
                    reasons=_reasons_from(trace),
                    policy_version=policy_version,
                    trace=adj_trace,
                )
        return RiskProposal(
            status=ProposalStatus.REJECTED,
            approved_weight=0.0,
            reasons=_reasons_from(trace),
            policy_version=policy_version,
            trace=trace,
        )

    # REVIEW: treat as ADJUSTED to half weight (operator can override later)
    return RiskProposal(
        status=ProposalStatus.ADJUSTED,
        approved_weight=proposed_weight * 0.5,
        reasons=_reasons_from(trace),
        policy_version=policy_version,
        trace=trace,
    )
