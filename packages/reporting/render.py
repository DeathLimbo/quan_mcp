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
    market_failures: Sequence[str] | None = None,
) -> str:
    lines: list[str] = []
    # Degraded header (spec §38 系统: 部分市场失败时组合报告明确降级而非伪造完整结果).
    degraded = bool(no_forecasts) or bool(market_failures)
    if degraded:
        lines.append(f"# Daily Report — {as_of.isoformat()}  ⚠️ DEGRADED")
        lines.append("")
        lines.append("> **本报告已降级**：以下标的/市场无法生成预测，结果不完整。")
        if market_failures:
            lines.append("> 市场失败: " + "; ".join(market_failures))
        if no_forecasts:
            lines.append(
                "> 无预测: "
                + ", ".join(nf.instrument_id.canonical() for nf in no_forecasts)
            )
    else:
        lines.append(f"# Daily Report — {as_of.isoformat()}")
    lines.append("")
    lines.append("## Forecasts")
    lines.append(
        "| Instrument | Local | FX | Base | Horizon | Model@Ver | DataVer |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for f in sorted(forecasts, key=lambda x: x.score, reverse=True):
        local = f"{f.expected_return_local:+.4f}" if f.expected_return_local is not None else "—"
        fx = f"{f.expected_fx_return:+.4f}" if f.expected_fx_return is not None else "0.0000"
        base = f"{f.expected_return_base:+.4f}" if f.expected_return_base is not None else f"{f.score:+.4f}"
        data_v = f.data_version or "—"
        lines.append(
            f"| {f.instrument_id.canonical()} | {local} | {fx} | {base} "
            f"| {f.horizon_days}d | {f.model_id}@{f.model_version} | {data_v} |"
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
