"""Observability metrics (§98) — Prometheus surface shared by the app.

Metrics are lazily registered once. Metric names follow the spec taxonomy:
- data_lag_seconds{market,dataset}
- rolling_ic{family,horizon}
- no_forecast_count_total{reason}
- risk_rejection_count_total{layer,code}
- portfolio_drawdown_ratio{portfolio_id}
- job_duration_seconds{job_type}

Values are set via the small helper functions below so producers do not
touch the Prometheus client API directly. The FastAPI app already exposes
``/metrics`` via ``prometheus_client.generate_latest``.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Shared registry — imports must be idempotent so we lazily construct it.
_REGISTRY = CollectorRegistry(auto_describe=True)


data_lag_seconds = Gauge(
    "data_lag_seconds", "Age of latest bar vs wall clock",
    ["market", "dataset"], registry=_REGISTRY,
)

rolling_ic = Gauge(
    "rolling_ic", "Rolling information coefficient by family & horizon",
    ["family", "horizon"], registry=_REGISTRY,
)

no_forecast_count = Counter(
    "no_forecast_count_total", "NO_FORECAST emissions grouped by reason",
    ["reason"], registry=_REGISTRY,
)

risk_rejection_count = Counter(
    "risk_rejection_count_total", "Risk-layer rejections by layer and code",
    ["layer", "code"], registry=_REGISTRY,
)

portfolio_drawdown_ratio = Gauge(
    "portfolio_drawdown_ratio", "Trailing peak-to-current drawdown per portfolio",
    ["portfolio_id"], registry=_REGISTRY,
)

job_duration_seconds = Histogram(
    "job_duration_seconds", "Duration of admin/worker jobs",
    ["job_type"], registry=_REGISTRY,
    buckets=(0.1, 0.5, 1, 5, 15, 60, 300, 900, 3600),
)


def registry() -> CollectorRegistry:
    return _REGISTRY


# --- Convenience setters (small enough to inline, but centralized so
# callers can be renamed/monitored without touching every producer).

def record_no_forecast(reason: str) -> None:
    no_forecast_count.labels(reason=reason).inc()


def record_risk_rejection(layer: str, code: str) -> None:
    risk_rejection_count.labels(layer=layer, code=code).inc()


def set_data_lag(market: str, dataset: str, seconds: float) -> None:
    data_lag_seconds.labels(market=market, dataset=dataset).set(seconds)


def set_rolling_ic(family: str, horizon: int, ic: float) -> None:
    rolling_ic.labels(family=family, horizon=str(horizon)).set(ic)


def set_portfolio_drawdown(portfolio_id: str, dd: float) -> None:
    portfolio_drawdown_ratio.labels(portfolio_id=portfolio_id).set(dd)
