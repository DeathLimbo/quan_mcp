"""Weekly auto-review闭环：系统每周自我复盘.

This is the "self-reflection" core of the self-improving quant loop.
Every week the system reviews its own performance: IC trend, drift events,
retrain/rollback actions, forecast accuracy — and produces a structured
report that feeds back into the next iteration.

Spec §27 (复盘) + 自我思考核心.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Sequence

from packages.automation.auto_retrain import RetrainEvent
from packages.automation.ic_guard import RollbackEvent


@dataclass(frozen=True, slots=True)
class WeeklyReview:
    """One week of self-reflection."""
    week_start: date
    week_end: date
    model_id: str
    version: str

    # performance
    ic_this_week: float
    ic_trend_8w: tuple[float, ...]
    hit_rate: float
    forecast_count: int

    # events
    drift_retrains: tuple[RetrainEvent, ...]
    rollbacks: tuple[RollbackEvent, ...]

    # verdict
    verdict: str  # "healthy" / "degrading" / "critical"

    @property
    def ic_change(self) -> float:
        """IC change vs 8 weeks ago. Positive = improving."""
        if len(self.ic_trend_8w) < 2:
            return 0.0
        return self.ic_trend_8w[-1] - self.ic_trend_8w[0]

    def to_markdown(self) -> str:
        """Generate a structured markdown review report."""
        lines = [
            f"# 周度复盘 · {self.week_start} ~ {self.week_end}",
            "",
            f"**模型**: {self.model_id}@{self.version}",
            f"**状态**: {self.verdict}",
            "",
            "## 表现",
            f"- 本周 IC: {self.ic_this_week:+.4f}",
            f"- 8 周 IC 趋势: {' → '.join(f'{ic:+.3f}' for ic in self.ic_trend_8w)}",
            f"- IC 变化: {self.ic_change:+.4f} ({'改善' if self.ic_change > 0 else '衰减'})",
            f"- 命中率: {self.hit_rate:.1%} ({self.forecast_count} 预测)",
            "",
            "## 事件",
        ]
        if self.drift_retrains:
            lines.append(f"- 漂移触发重训练: {len(self.drift_retrains)} 次")
            for e in self.drift_retrains:
                lines.append(f"  - {e.triggered_at:%m-%d %H:%M} drift={e.drift_level.value} → {e.new_model_id}@{e.new_version[:8]} ({e.new_state})")
        else:
            lines.append("- 漂移触发重训练: 0 次")

        if self.rollbacks:
            lines.append(f"- IC 衰减降级: {len(self.rollbacks)} 次")
            for e in self.rollbacks:
                lines.append(f"  - {e.triggered_at:%m-%d %H:%M} {e.reason[:80]}")
        else:
            lines.append("- IC 衰减降级: 0 次")

        lines.extend([
            "",
            "## 判定",
        ])
        if self.verdict == "healthy":
            lines.append("模型表现稳定，IC 在阈值之上，无需干预。")
        elif self.verdict == "degrading":
            lines.append("**注意**: IC 正在衰减，接近阈值。观察下周，若继续衰减将触发自动降级。")
        else:
            lines.append("**警告**: IC 已跌破阈值，已触发自动降级。需人工介入分析根因。")

        return "\n".join(lines)


@dataclass
class WeeklyReviewer:
    """Generate weekly self-reflection reviews.

    Pulls together IC history, drift/retrain events, and rollback events
    into a single structured review. The review's verdict drives the next
    iteration's behavior.
    """
    audit: Any  # AuditLog — to record the review itself
    ic_healthy_threshold: float = 0.05
    ic_critical_threshold: float = 0.0

    def review(
        self,
        *,
        week_start: date,
        week_end: date,
        model_id: str,
        version: str,
        ic_history: Sequence[float],  # chronological, latest last
        hit_rate: float,
        forecast_count: int,
        drift_retrains: Sequence[RetrainEvent] = (),
        rollbacks: Sequence[RollbackEvent] = (),
    ) -> WeeklyReview:
        """Produce a WeeklyReview from the week's data."""
        ic_this_week = ic_history[-1] if ic_history else 0.0
        ic_trend = tuple(ic_history[-8:]) if len(ic_history) >= 8 else tuple(ic_history)

        # verdict logic
        if ic_this_week >= self.ic_healthy_threshold:
            verdict = "healthy"
        elif ic_this_week >= self.ic_critical_threshold:
            verdict = "degrading"
        else:
            verdict = "critical"

        review = WeeklyReview(
            week_start=week_start,
            week_end=week_end,
            model_id=model_id,
            version=version,
            ic_this_week=ic_this_week,
            ic_trend_8w=ic_trend,
            hit_rate=hit_rate,
            forecast_count=forecast_count,
            drift_retrains=tuple(drift_retrains),
            rollbacks=tuple(rollbacks),
            verdict=verdict,
        )

        # audit the review itself
        self.audit.record(
            actor_id="weekly-reviewer@system",
            actor_type="system",
            action="weekly_review_generated",
            resource_type="model",
            resource_id=f"{model_id}:{version}",
            after={
                "week": f"{week_start}~{week_end}",
                "verdict": verdict,
                "ic": round(ic_this_week, 4),
                "retrains": len(drift_retrains),
                "rollbacks": len(rollbacks),
            },
        )

        return review
