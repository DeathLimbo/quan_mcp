"""Auto-retrain闭环：drift检测 → 自动重训练 → 注册新版本.

This is the "self-correcting" half of the self-improving quant loop.
When drift crosses ALERT/HALT, a fresh model is trained on the latest
data and registered as DRAFT for the promotion gate to evaluate.

Spec §17 (multi-market scheduling) + §19 (drift detection) + §21 (champion-challenger).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Sequence

from packages.drift.metrics import DriftLevel, DriftReport


@dataclass(frozen=True, slots=True)
class RetrainEvent:
    """Record of one auto-retrain trigger."""
    triggered_at: datetime
    drift_level: DriftLevel
    reason: str
    new_model_id: str
    new_version: str
    new_state: str  # "DRAFT" — awaits promotion gate


@dataclass
class AutoRetrainEngine:
    """Monitor drift reports; retrain when level >= threshold.

    Rate-limited: won't retrain more than once per ``min_interval_days`` for
    the same model, even if drift stays HIGH.
    """
    registry: Any        # ModelRegistry (InMemory or DB-backed)
    audit: Any           # AuditLog
    admin: Any           # AdminTools (for register_model)
    train_fn: Callable[[], Any]  # () -> trained Model instance
    threshold: DriftLevel = DriftLevel.ALERT
    min_interval_days: int = 7
    _last_retrain: dict[str, datetime] = field(default_factory=dict)

    def check_and_retrain(
        self,
        *,
        production_model_id: str,
        drift_report: DriftReport,
    ) -> RetrainEvent | None:
        """If drift >= threshold and rate-limit allows, retrain + register DRAFT.

        Returns None when no retrain is needed (drift below threshold or
        rate-limited). Returns RetrainEvent when a new DRAFT was registered.
        """
        level = drift_report.worst_level()
        order = [DriftLevel.OK, DriftLevel.WATCH, DriftLevel.ALERT, DriftLevel.HALT]
        if order.index(level) < order.index(self.threshold):
            return None  # drift not severe enough

        # rate limit
        now = datetime.now(timezone.utc)
        last = self._last_retrain.get(production_model_id)
        if last is not None and (now - last).days < self.min_interval_days:
            return None  # too soon since last retrain

        # retrain
        new_model = self.train_fn()
        self._last_retrain[production_model_id] = now

        # register as DRAFT via AdminTools
        rec = self.admin.register_model(
            model_id=new_model.model_id,
            version=new_model.version,
            market="CN",
            horizon_days=getattr(new_model, "horizon_days", 20),
            feature_set_hash=new_model.feature_set_hash,
            actor="auto-retrain@system",
            notes=f"Auto-retrain triggered by drift={level.value} on {production_model_id}",
        )
        # attach the artifact so shadow/promotion can use it
        self.registry._artifacts[(new_model.model_id, new_model.version)] = new_model

        event = RetrainEvent(
            triggered_at=now,
            drift_level=level,
            reason=f"drift worst_level={level.value}; PSI={max(drift_report.feature_psi.values(), default=0):.3f}",
            new_model_id=new_model.model_id,
            new_version=new_model.version,
            new_state=rec["data"]["state"],
        )

        # audit log
        self.audit.record(
            actor_id="auto-retrain@system",
            actor_type="system",
            action="auto_retrain_triggered",
            resource_type="model",
            resource_id=f"{new_model.model_id}:{new_model.version}",
            after={
                "production_model": production_model_id,
                "drift_level": level.value,
                "new_model": new_model.model_id,
                "new_version": new_model.version,
            },
        )
        return event
