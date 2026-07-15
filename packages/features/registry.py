"""Feature registry with content hashing for reproducibility."""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    version: str
    lookback_days: int          # min bars required
    is_point_in_time: bool
    fn: Callable[..., float | None]
    source_hash: str            # sha256 of fn source; part of dataset fingerprint

    def compute(self, *args, **kwargs) -> float | None:
        return self.fn(*args, **kwargs)


class FeatureRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> None:
        if spec.name in self._by_name:
            raise ValueError(f"feature {spec.name!r} already registered")
        self._by_name[spec.name] = spec

    def get(self, name: str) -> FeatureSpec:
        if name not in self._by_name:
            raise KeyError(f"feature {name!r} not registered")
        return self._by_name[name]

    def all(self) -> list[FeatureSpec]:
        return list(self._by_name.values())

    def by_names(self, names: Iterable[str]) -> list[FeatureSpec]:
        return [self.get(n) for n in names]


registry = FeatureRegistry()


def feature(
    name: str,
    *,
    version: str = "v1",
    lookback_days: int,
    is_point_in_time: bool = True,
) -> Callable[[Callable], Callable]:
    def _wrap(fn: Callable) -> Callable:
        src = inspect.getsource(fn)
        h = hashlib.sha256(f"{name}|{version}|{src}".encode("utf-8")).hexdigest()
        spec = FeatureSpec(
            name=name, version=version,
            lookback_days=lookback_days, is_point_in_time=is_point_in_time,
            fn=fn, source_hash=h,
        )
        registry.register(spec)
        fn.feature_spec = spec  # type: ignore[attr-defined]
        return fn
    return _wrap
