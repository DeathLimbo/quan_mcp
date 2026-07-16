"""FeatureSet: the *only* thing training and inference call.

Given a chronological bar sequence and an as-of timestamp, compute all
requested features PIT-safely. Same code path in both regimes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import ensure_utc
from packages.data_sources.contracts import Bar
from packages.features.registry import FeatureSpec, FundamentalContext, registry


@dataclass(frozen=True, slots=True)
class FeatureSet:
    names: tuple[str, ...]

    @property
    def specs(self) -> list[FeatureSpec]:
        return registry.by_names(self.names)

    @property
    def max_lookback(self) -> int:
        return max(s.lookback_days for s in self.specs)

    @property
    def content_hash(self) -> str:
        h = hashlib.sha256()
        for s in self.specs:
            h.update(f"{s.name}|{s.version}|{s.source_hash};".encode())
        return h.hexdigest()

    def compute(self, bars: Sequence[Bar], as_of: datetime,
                *, fund_ctx: FundamentalContext | None = None) -> dict[str, float | None]:
        """Slice bars to those available at ``as_of``, then evaluate features.

        ``fund_ctx`` carries PIT-safe fundamentals for features that declared
        ``requires_fundamentals=True``. When such a feature is present but
        ``fund_ctx`` is None, it resolves to None (fail-closed) rather than
        raising — so a pure-bars FeatureSet still computes cleanly.
        """
        as_of = ensure_utc(as_of)
        visible = [b for b in bars if b.available_at_utc <= as_of]
        if not visible:
            raise FeatureMissingError(f"no bars available at {as_of.isoformat()}")
        # Ensure chronological
        visible = sorted(visible, key=lambda b: b.market_local_date)
        out: dict[str, float | None] = {}
        for spec in self.specs:
            window = visible[-spec.lookback_days:] if spec.lookback_days else visible
            if spec.requires_fundamentals:
                if fund_ctx is None:
                    out[spec.name] = None
                else:
                    out[spec.name] = spec.compute(window, fund_ctx)
            else:
                out[spec.name] = spec.compute(window)
        return out


def compute_features(names: Sequence[str], bars: Sequence[Bar], as_of: datetime,
                     *, fund_ctx: FundamentalContext | None = None) -> dict[str, float | None]:
    return FeatureSet(tuple(names)).compute(bars, as_of, fund_ctx=fund_ctx)
