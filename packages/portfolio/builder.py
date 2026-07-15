"""Score-proportional portfolio builder with hard constraints."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from packages.common.instrument_id import InstrumentId
from packages.inference.service import Forecast


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    max_name_weight: float = 0.10
    gross_cap: float = 1.0
    long_only: bool = True
    top_k: int | None = None
    min_score: float = 0.0    # scores below this are dropped
    cash_sleeve: float = 0.0  # 0.05 -> keep 5% cash


@dataclass(frozen=True, slots=True)
class PortfolioTarget:
    weights: dict[InstrumentId, float] = field(default_factory=dict)
    cash: float = 0.0

    @property
    def gross(self) -> float:
        return sum(abs(w) for w in self.weights.values())


def build_portfolio(
    forecasts: Sequence[Forecast],
    cfg: PortfolioConfig | None = None,
) -> PortfolioTarget:
    cfg = cfg or PortfolioConfig()
    if not forecasts:
        return PortfolioTarget(weights={}, cash=1.0)

    # Rank & select
    pool = list(forecasts)
    if cfg.long_only:
        pool = [f for f in pool if f.score > cfg.min_score]
    pool.sort(key=lambda f: f.score, reverse=True)
    if cfg.top_k is not None:
        pool = pool[: cfg.top_k]

    if not pool:
        return PortfolioTarget(weights={}, cash=1.0)

    # Score-proportional raw weights (positive scores only for long-only)
    scores = [max(f.score, 0.0) for f in pool] if cfg.long_only else [f.score for f in pool]
    total_abs = sum(abs(s) for s in scores)
    if total_abs == 0:
        return PortfolioTarget(weights={}, cash=1.0)

    available = max(cfg.gross_cap - cfg.cash_sleeve, 0.0)
    raw = {f.instrument_id: (scores[i] / total_abs) * available for i, f in enumerate(pool)}

    # Clip per-name, then re-scale to preserve gross cap when possible
    clipped = {iid: max(min(w, cfg.max_name_weight), -cfg.max_name_weight if not cfg.long_only else 0.0)
               for iid, w in raw.items()}
    gross_after_clip = sum(abs(w) for w in clipped.values())
    if gross_after_clip == 0:
        return PortfolioTarget(weights={}, cash=1.0)
    scale = min(1.0, available / gross_after_clip) if gross_after_clip > available else 1.0
    final = {iid: w * scale for iid, w in clipped.items()}
    cash = 1.0 - sum(abs(w) for w in final.values())
    return PortfolioTarget(weights=final, cash=cash)


def build_portfolio_from_scores(
    scores: dict[InstrumentId, float],
    cfg: PortfolioConfig | None = None,
) -> PortfolioTarget:
    """Score-only variant used by MCP proposal endpoints.

    Same math as :func:`build_portfolio` but skips the full Forecast plumbing
    when the caller only has a scalar per instrument.
    """
    cfg = cfg or PortfolioConfig()
    if not scores:
        return PortfolioTarget(weights={}, cash=1.0)
    pool = [(iid, s) for iid, s in scores.items()
            if not cfg.long_only or s > cfg.min_score]
    pool.sort(key=lambda kv: kv[1], reverse=True)
    if cfg.top_k is not None:
        pool = pool[: cfg.top_k]
    if not pool:
        return PortfolioTarget(weights={}, cash=1.0)
    raw_scores = [max(s, 0.0) for _, s in pool] if cfg.long_only else [s for _, s in pool]
    total_abs = sum(abs(s) for s in raw_scores) or 1.0
    available = max(cfg.gross_cap - cfg.cash_sleeve, 0.0)
    raw = {iid: (raw_scores[i] / total_abs) * available
           for i, (iid, _) in enumerate(pool)}
    clipped = {iid: max(min(w, cfg.max_name_weight),
                        -cfg.max_name_weight if not cfg.long_only else 0.0)
               for iid, w in raw.items()}
    gross = sum(abs(w) for w in clipped.values())
    if gross == 0:
        return PortfolioTarget(weights={}, cash=1.0)
    scale = min(1.0, available / gross) if gross > available else 1.0
    final = {iid: w * scale for iid, w in clipped.items()}
    cash = 1.0 - sum(abs(w) for w in final.values())
    return PortfolioTarget(weights=final, cash=cash)
