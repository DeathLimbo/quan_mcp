from __future__ import annotations

from packages.common.instrument_id import AssetType
from packages.portfolio.target import (
    TargetStatus,
    evaluate_return_target,
)


def test_monthly_ten_percent_fund_target_is_not_feasible():
    assessment = evaluate_return_target(
        target_return=0.10,
        horizon_days=30,
        asset_type=AssetType.FUND,
        share_class="C",
    )

    assert assessment.status is TargetStatus.TARGET_NOT_FEASIBLE
    assert assessment.recommendation_allowed is False
    assert assessment.research_only is True
    assert assessment.max_candidate_weight == 0.0
    assert assessment.annualized_target > 2.0
    assert "target:monthly_10pct_extreme" in assessment.reasons
    assert "policy:no_guaranteed_return" in assessment.reasons
    assert "asset:fund_c_requires_fee_and_holding_period_review" in assessment.reasons


def test_modest_target_allows_screening_without_guarantee():
    assessment = evaluate_return_target(
        target_return=0.01,
        horizon_days=30,
        asset_type=AssetType.FUND,
        share_class="A",
    )

    assert assessment.status is TargetStatus.SCREENING_ALLOWED
    assert assessment.recommendation_allowed is True
    assert assessment.research_only is False
    assert assessment.max_candidate_weight > 0.0
    assert "policy:no_guaranteed_return" in assessment.reasons
