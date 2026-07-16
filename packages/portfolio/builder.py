"""Score-proportional portfolio builder with hard constraints."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Sequence

from packages.common.instrument_id import InstrumentId
from packages.fx.converter import FxConverter, FxNotAvailableError
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


# ---- cross-currency exposure (spec §29 仓位与币种管理) ---------------------

@dataclass(frozen=True, slots=True)
class PositionExposure:
    """Per-position currency attribution (spec §29).

    A US equity position must be reported in both USD local market value and
    base-currency market value, with the USD leg flagged as FX risk.
    """
    instrument_id: InstrumentId
    local_ccy: str
    weight: float                  # portfolio weight (base ccy fraction)
    local_market_value: float      # MV in the instrument's local currency
    base_market_value: float       # MV in the portfolio base currency
    fx_exposure: float             # base-ccy MV exposed to FX (=0 if same ccy)


def attribute_exposures(
    weights: dict[InstrumentId, float],
    *,
    ccy_map: dict[InstrumentId, str],
    fx_converter: FxConverter | None,
    as_of: date,
    equity: float = 1.0,
) -> list[PositionExposure]:
    """Attribute each weight into local + base market value (§29).

    ``weights`` are base-currency fractions; ``ccy_map`` gives each
    instrument's local currency; ``equity`` is total base-currency equity.
    Fail-closed: a missing FX rate leaves ``local_market_value`` as the base
    MV — we never fabricate a conversion.
    """
    out: list[PositionExposure] = []
    base_ccy = fx_converter.base_ccy if fx_converter is not None else "CNY"
    for iid, w in weights.items():
        local_ccy = ccy_map.get(iid, base_ccy)
        base_mv = w * equity
        if local_ccy == base_ccy or fx_converter is None:
            out.append(PositionExposure(
                instrument_id=iid, local_ccy=local_ccy, weight=w,
                local_market_value=base_mv, base_market_value=base_mv,
                fx_exposure=0.0,
            ))
            continue
        # cross-currency: convert base MV → local MV at the PIT rate
        try:
            local_mv = float(fx_converter.convert(
                Decimal(str(base_mv)),
                from_ccy=base_ccy, to_ccy=local_ccy, on_or_before=as_of))
        except FxNotAvailableError:
            local_mv = base_mv  # fail-closed: cannot convert
        out.append(PositionExposure(
            instrument_id=iid, local_ccy=local_ccy, weight=w,
            local_market_value=local_mv, base_market_value=base_mv,
            fx_exposure=base_mv,  # entire base MV sits under FX risk
        ))
    return out


def currency_exposure(
    weights: dict[InstrumentId, float],
    *,
    ccy_map: dict[InstrumentId, str],
    fx_converter: FxConverter | None,
    as_of: date,
) -> dict[str, float]:
    """Aggregate base-currency market value by instrument currency (§29 币种上限).

    Returns ``{ccy: total_base_mv_fraction}`` so a risk layer can enforce a
    per-currency cap. Foreign currencies sum to their converted base MV.
    """
    agg: dict[str, float] = {}
    for exp in attribute_exposures(
        weights, ccy_map=ccy_map, fx_converter=fx_converter, as_of=as_of,
    ):
        agg[exp.local_ccy] = agg.get(exp.local_ccy, 0.0) + exp.base_market_value
    return agg
