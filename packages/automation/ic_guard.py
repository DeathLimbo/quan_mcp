"""IC guard闭环：PRODUCTION模型IC连续衰减 → 自动request_rollback.

This is the "self-protection" half of the self-improving quant loop.
When the production model's OOS IC stays below threshold for N consecutive
evaluation periods, a rollback is requested to prevent trading on a
stale model.

Spec §21 (champion-challenger失效保护) + §27 (复盘).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class RollbackEvent:
    """Record of one auto-rollback trigger."""
    triggered_at: datetime
    model_id: str
    version: str
    reason: str
    ic_history: tuple[float, ...]
    request_id: str | None


@dataclass
class ICGuard:
    """Monitor PRODUCTION model IC; request rollback on sustained decay.

    Args:
        registry: ModelRegistry
        audit: AuditLog
        admin: AdminTools (for model_request_rollback)
        ic_threshold: IC below this is "decay" (default 0.05)
        consecutive_periods: how many consecutive decay periods before rollback
    """
    registry: Any
    audit: Any
    admin: Any
    ic_threshold: float = 0.05
    consecutive_periods: int = 4

    def check_and_rollback(
        self,
        *,
        model_id: str,
        version: str,
        ic_history: Sequence[float],
        actor: str = "ic-guard@system",
    ) -> RollbackEvent | None:
        """If IC < threshold for N consecutive periods, request rollback.

        Returns None when IC is healthy or decay hasn't persisted long enough.
        Returns RollbackEvent when a rollback was requested.
        """
        if len(ic_history) < self.consecutive_periods:
            return None  # not enough history to judge

        recent = list(ic_history[-self.consecutive_periods:])
        if any(ic >= self.ic_threshold for ic in recent):
            return None  # at least one period was healthy — no rollback

        # all recent periods below threshold → request rollback
        now = datetime.now(timezone.utc)
        reason = (f"IC below {self.ic_threshold} for {self.consecutive_periods} "
                  f"consecutive periods: {[round(ic, 4) for ic in recent]}")

        request_id = None
        try:
            r = self.admin.model_request_rollback(
                model_id=model_id, version=version, actor=actor, reason=reason)
            if r.get("ok"):
                request_id = r.get("data", {}).get("request_id")
        except Exception:
            pass  # admin may not have request_rollback; log and continue

        self.audit.record(
            actor_id=actor,
            actor_type="system",
            action="ic_guard_rollback_triggered",
            resource_type="model",
            resource_id=f"{model_id}:{version}",
            after={
                "ic_history": [round(ic, 4) for ic in recent],
                "threshold": self.ic_threshold,
                "request_id": request_id,
            },
        )

        return RollbackEvent(
            triggered_at=now,
            model_id=model_id,
            version=version,
            reason=reason,
            ic_history=tuple(recent),
            request_id=request_id,
        )
