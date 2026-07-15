"""Adapter registry. Keeps runtime pluggable; no import cycles.

Usage:
    from packages.data_sources import register_adapter, get_adapter
    register_adapter("akshare", AkshareAdapter())
    adapter = get_adapter("akshare")
"""
from __future__ import annotations

from typing import Any

_REGISTRY: dict[str, Any] = {}


def register_adapter(name: str, adapter: Any) -> None:
    if name in _REGISTRY:
        raise ValueError(f"adapter {name!r} already registered")
    _REGISTRY[name] = adapter


def get_adapter(name: str) -> Any:
    if name not in _REGISTRY:
        raise KeyError(f"adapter {name!r} not registered")
    return _REGISTRY[name]


def list_adapters() -> list[str]:
    return sorted(_REGISTRY.keys())


class AdapterRegistry:
    """Small object-shaped wrapper for wiring into ``app.state``.

    Prod hosts build one at startup with the adapters they want to expose.
    Tests build one from a plain dict — no import cycles, no globals.
    """

    def __init__(self, adapters: dict[str, Any] | None = None) -> None:
        self._adapters: dict[str, Any] = dict(adapters or {})

    def register(self, name: str, adapter: Any) -> None:
        if name in self._adapters:
            raise ValueError(f"adapter {name!r} already registered")
        self._adapters[name] = adapter

    def get(self, name: str) -> Any:
        if name not in self._adapters:
            raise KeyError(f"adapter {name!r} not registered")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters.keys())


def _reset_for_tests() -> None:
    _REGISTRY.clear()
