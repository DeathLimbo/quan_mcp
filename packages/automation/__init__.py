"""Automation package — self-improving quant feedback loops.

Three closed loops that turn an automated pipeline into a self-correcting,
self-reflecting system:

- AutoRetrainEngine: drift → auto-retrain → register new DRAFT (spec §19+§21)
- ICGuard: IC decay → auto-request-rollback (spec §21失效保护)
- WeeklyReviewer: weekly self-reflection report (spec §27复盘)

Together they form the "self-thinking" feedback layer.
"""
from packages.automation.auto_retrain import AutoRetrainEngine, RetrainEvent
from packages.automation.ic_guard import ICGuard, RollbackEvent
from packages.automation.weekly_review import WeeklyReviewer, WeeklyReview

__all__ = [
    "AutoRetrainEngine", "RetrainEvent",
    "ICGuard", "RollbackEvent",
    "WeeklyReviewer", "WeeklyReview",
]
