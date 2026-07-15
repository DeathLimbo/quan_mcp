"""API routers bundle.

Each router is a small FastAPI ``APIRouter`` that exposes one bounded context.
Handlers are thin wrappers over packages/* services; response envelope is the
standard ``ok/err`` from packages.common.response so every endpoint is
uniform, testable, and audit-friendly.

Routers are wired into ``apps/api/main.py`` at module import time.
"""
from __future__ import annotations

from .instruments import router as instruments_router
from .markets import router as markets_router
from .forecast import router as forecast_router
from .portfolio import router as portfolio_router
from .data_status import router as data_status_router
from .fundamentals import router as fundamentals_router
from .admin_ingestion import router as admin_ingestion_router
from .scheduler import router as scheduler_router

__all__ = [
    "instruments_router",
    "markets_router",
    "forecast_router",
    "portfolio_router",
    "data_status_router",
    "fundamentals_router",
    "admin_ingestion_router",
    "scheduler_router",
]
