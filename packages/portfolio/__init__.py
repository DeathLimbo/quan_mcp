"""Portfolio construction.

Turn a list of ``Forecast`` (with scores) into target weights, respecting:
- max per-name exposure (frac)
- gross exposure cap
- long-only or long-short
- cash sleeve

v1: score-proportional weighting with clip + renormalize. Optimizer hook is
left open (spec §组合构建) so a mean-variance / risk-parity backend can slot
in later without changing callers.
"""
from packages.portfolio.builder import (
    PortfolioConfig, PortfolioTarget, build_portfolio,
)
from packages.portfolio.target import (
    ReturnTargetAssessment, TargetStatus, evaluate_return_target,
)

__all__ = [
    "PortfolioConfig", "PortfolioTarget", "build_portfolio",
    "ReturnTargetAssessment", "TargetStatus", "evaluate_return_target",
]
