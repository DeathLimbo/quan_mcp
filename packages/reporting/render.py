"""Markdown renderers. Deterministic string builders. No I/O."""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from packages.inference.service import Forecast, NoForecast
from packages.portfolio.builder import PortfolioTarget
from packages.risk.engine import RiskDecision


def render_daily_report(
    *,
    as_of: datetime,
    forecasts: Sequence[Forecast],
    no_forecasts: Sequence[NoForecast],
    portfolio: PortfolioTarget,
    metrics: dict[str, float] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Daily Report — {as_of.isoformat()}")
    lines.append("")
    lines.append("## Forecasts")
    lines.append("| Instrument | Score | Horizon | Model | FeatureHash |")
    lines.append("|---|---|---|---|---|")
    for f in sorted(forecasts, key=lambda x: x.score, reverse=True):
        lines.append(
            f"| {f.instrument_id.canonical()} | {f.score:+.4f} | {f.horizon_days}d "
            f"| {f.model_id}@{f.model_version} | {f.feature_hash[:12]} |"
        )
    if no_forecasts:
        lines.append("")
        lines.append("## NO_FORECAST (fail-closed)")
        for nf in no_forecasts:
            lines.append(f"- {nf.instrument_id.canonical()}: **{nf.reason.value}** — {nf.detail}")

    lines.append("")
    lines.append("## Portfolio")
    lines.append(f"- gross = {portfolio.gross:.4f}, cash = {portfolio.cash:.4f}")
    lines.append("| Instrument | Weight |")
    lines.append("|---|---|")
    for iid, w in sorted(portfolio.weights.items(), key=lambda kv: -abs(kv[1])):
        lines.append(f"| {iid.canonical()} | {w:+.4f} |")

    if metrics:
        lines.append("")
        lines.append("## Metrics")
        for k, v in metrics.items():
            lines.append(f"- **{k}**: {v:.4f}")
    return "\n".join(lines) + "\n"


def render_risk_trace(trace: Sequence[RiskDecision]) -> str:
    lines = ["| Layer | Verdict | Code | Reason |", "|---|---|---|---|"]
    for d in trace:
        lines.append(f"| {d.layer} | {d.verdict.value} | {d.code} | {d.reason} |")
    return "\n".join(lines) + "\n"


def render_backtest_summary(*, total_return: float, sharpe: float, mdd: float,
                            hit_rate: float, n_trades: int) -> str:
    return (
        "## Backtest Summary\n"
        f"- Total return: {total_return:+.2%}\n"
        f"- Sharpe (ann.): {sharpe:.2f}\n"
        f"- Max drawdown: {mdd:+.2%}\n"
        f"- Hit rate: {hit_rate:.2%}\n"
        f"- Trades: {n_trades}\n"
    )
