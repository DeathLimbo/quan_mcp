"""Target-return feasibility checks for portfolio requests.

This layer gates user intent before forecast screening or portfolio building.
It does not predict returns; it decides whether a requested target is within
policy bounds for automated recommendation workflows.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from packages.common.instrument_id import AssetType


class TargetStatus(str, Enum):
    SCREENING_ALLOWED = "SCREENING_ALLOWED"
    TARGET_NOT_FEASIBLE = "TARGET_NOT_FEASIBLE"


@dataclass(frozen=True, slots=True)
class ReturnTargetAssessment:
    status: TargetStatus
    target_return: float
    horizon_days: int
    annualized_target: float
    recommendation_allowed: bool
    research_only: bool
    max_candidate_weight: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "target_return": self.target_return,
            "horizon_days": self.horizon_days,
            "annualized_target": self.annualized_target,
            "recommendation_allowed": self.recommendation_allowed,
            "research_only": self.research_only,
            "max_candidate_weight": self.max_candidate_weight,
            "reasons": list(self.reasons),
        }


def evaluate_return_target(
    *,
    target_return: float,
    horizon_days: int,
    asset_type: AssetType | None = None,
    share_class: str | None = None,
    allow_high_risk: bool = False,
    max_candidate_weight: float = 0.10,
) -> ReturnTargetAssessment:
    """Assess whether a target-return request can proceed to recommendation.

    ``allow_high_risk`` records user intent, but it does not override hard
    feasibility limits. A 10% monthly target is treated as research-only
    because the system must not promise that outcome.
    """
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    if target_return <= -1.0:
        raise ValueError("target_return must be greater than -100%")

    annualized = (1.0 + float(target_return)) ** (365.0 / horizon_days) - 1.0
    reasons = ["policy:no_guaranteed_return"]
    hard_block = False

    share = share_class.strip().upper() if share_class else None
    if asset_type is AssetType.FUND and share == "C":
        reasons.append("asset:fund_c_requires_fee_and_holding_period_review")

    if horizon_days <= 31 and target_return >= 0.10:
        reasons.append("target:monthly_10pct_extreme")
        hard_block = True
    if annualized >= 1.0:
        reasons.append("target:annualized_above_policy_limit")
        hard_block = True
    if hard_block and allow_high_risk:
        reasons.append("policy:high_risk_does_not_override_hard_limits")

    if hard_block:
        return ReturnTargetAssessment(
            status=TargetStatus.TARGET_NOT_FEASIBLE,
            target_return=float(target_return),
            horizon_days=horizon_days,
            annualized_target=annualized,
            recommendation_allowed=False,
            research_only=True,
            max_candidate_weight=0.0,
            reasons=tuple(reasons),
        )

    return ReturnTargetAssessment(
        status=TargetStatus.SCREENING_ALLOWED,
        target_return=float(target_return),
        horizon_days=horizon_days,
        annualized_target=annualized,
        recommendation_allowed=True,
        research_only=False,
        max_candidate_weight=max_candidate_weight,
        reasons=tuple(reasons),
    )
