"""Reporting: deterministic Markdown research/decision reports.

A report is a plain string built from typed inputs (Forecasts, RiskDecisions,
PortfolioTarget, metrics) so it is auditable and reproducible from the same
inputs. No external template engine dependency.
"""
from packages.reporting.render import (
    render_daily_report, render_risk_trace, render_backtest_summary,
)

__all__ = ["render_daily_report", "render_risk_trace", "render_backtest_summary"]
