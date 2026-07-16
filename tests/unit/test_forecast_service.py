"""ForecastService orchestrator tests (issue #5).

Validates the batch ``run()`` contract that adapts InferenceService.score()
to the FastAPI / MCP surface, including PIT as_of forwarding (issue #1).
"""
from __future__ import annotations

from datetime import datetime, timezone

from packages.common.instrument_id import Market, parse_instrument_id
from packages.inference.orchestrator import ForecastService
from packages.inference.service import (
    Forecast, NoForecast, NoForecastReason,
)


class _StubInference:
    """Duck-typed InferenceService: returns Forecast except for 'BAD' symbols."""

    def score(self, *, instrument_id, as_of, horizon_days, bars,
              fund_ctx=None, instrument_ccy=None):
        if instrument_id.symbol == "BAD":
            return NoForecast(
                instrument_id, as_of,
                NoForecastReason.NO_PRODUCTION_MODEL, "stub no model")
        return Forecast(
            instrument_id=instrument_id, as_of=as_of,
            horizon_days=horizon_days, score=0.5,
            model_id="m", model_version="v", feature_hash="hash",
        )


def test_run_aggregates_forecasts_and_no_forecasts():
    captured: list = []

    def bar_lookup(iid, start, end, as_of_utc=None):
        captured.append(as_of_utc)
        return []

    svc = ForecastService(_StubInference(), bar_lookup)
    iids = [
        parse_instrument_id("US.NASDAQ.EQUITY.AAPL"),
        parse_instrument_id("US.NYSE.EQUITY.BAD"),
    ]
    as_of = datetime(2026, 7, 16, 22, tzinfo=timezone.utc)
    fcs, nfs = svc.run(market=Market.US, horizon_days=5,
                       instruments=iids, as_of=as_of)
    assert len(fcs) == 1 and fcs[0].instrument_id.symbol == "AAPL"
    assert len(nfs) == 1 and nfs[0].instrument_id.symbol == "BAD"
    # issue #1: as_of must be forwarded to the bar lookup for PIT filtering
    assert captured == [as_of, as_of]


def test_run_defaults_as_of_to_now_when_omitted():
    svc = ForecastService(_StubInference(), lambda *a, **k: [])
    fcs, nfs = svc.run(
        market=Market.US, horizon_days=5,
        instruments=[parse_instrument_id("US.NASDAQ.EQUITY.AAPL")])
    # default as_of=now must still produce a forecast (no crash)
    assert len(fcs) == 1


def test_run_returns_empty_for_empty_instrument_list():
    svc = ForecastService(_StubInference(), lambda *a, **k: [])
    fcs, nfs = svc.run(market=Market.US, horizon_days=5, instruments=[])
    assert fcs == [] and nfs == []
