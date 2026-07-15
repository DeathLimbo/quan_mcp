"""Unit tests for packages.observability.metrics."""
from __future__ import annotations

from packages.observability import metrics as m


def _sample(metric, **labels):
    """Return the current numeric value for a labeled child."""
    return metric.labels(**labels)._value.get()


def test_data_lag_gauge_set() -> None:
    m.set_data_lag("CN", "market_bar", 42.0)
    assert _sample(m.data_lag_seconds, market="CN", dataset="market_bar") == 42.0
    # Overwriting semantics — gauges replace, not add.
    m.set_data_lag("CN", "market_bar", 5.0)
    assert _sample(m.data_lag_seconds, market="CN", dataset="market_bar") == 5.0


def test_rolling_ic_gauge() -> None:
    m.set_rolling_ic("CN_EQUITY_CROSS_SECTION_B", 5, 0.07)
    assert _sample(m.rolling_ic,
                   family="CN_EQUITY_CROSS_SECTION_B",
                   horizon="5") == 0.07


def test_no_forecast_counter_increments() -> None:
    before = _sample(m.no_forecast_count, reason="DATA_NOT_READY")
    m.record_no_forecast("DATA_NOT_READY")
    m.record_no_forecast("DATA_NOT_READY")
    after = _sample(m.no_forecast_count, reason="DATA_NOT_READY")
    assert after - before == 2


def test_risk_rejection_counter() -> None:
    before = _sample(m.risk_rejection_count, layer="L6", code="MAX_DAILY_LOSS")
    m.record_risk_rejection("L6", "MAX_DAILY_LOSS")
    after = _sample(m.risk_rejection_count, layer="L6", code="MAX_DAILY_LOSS")
    assert after - before == 1


def test_portfolio_drawdown_gauge() -> None:
    m.set_portfolio_drawdown("PF-1", 0.12)
    assert _sample(m.portfolio_drawdown_ratio, portfolio_id="PF-1") == 0.12


def test_registry_returns_shared_object() -> None:
    r1 = m.registry()
    r2 = m.registry()
    assert r1 is r2
