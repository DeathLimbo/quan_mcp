"""Shared FastAPI dependency providers.

Keeps stateful collaborators (instrument service, DB engine) at module
scope so tests can override them via ``app.dependency_overrides``.
"""
from __future__ import annotations

from packages.instruments.service import (
    InMemoryInstrumentRepository, InstrumentService,
)

# Process-wide default instrument service (in-memory). Production hosts
# should override via ``app.dependency_overrides[get_instrument_service]``
# with a Sql-backed variant during startup.
_default_service = InstrumentService(InMemoryInstrumentRepository())


def get_instrument_service() -> InstrumentService:
    return _default_service


def set_default_instrument_service(svc: InstrumentService) -> None:
    """Test / bootstrap helper — swap the singleton without DI overrides."""
    global _default_service
    _default_service = svc
