"""Automation package — self-improving quant feedback loops.

- AutoRetrainEngine: drift → auto-retrain → register new DRAFT (spec §19+§21)
- ICGuard: IC decay → auto-request-rollback (spec §21失效保护)

These close the feedback loop that turns an automated pipeline into a
self-correcting system.
"""
from packages.automation.auto_retrain import AutoRetrainEngine, RetrainEvent
from packages.automation.ic_guard import ICGuard, RollbackEvent

__all__ = ["AutoRetrainEngine", "RetrainEvent", "ICGuard", "RollbackEvent"]
