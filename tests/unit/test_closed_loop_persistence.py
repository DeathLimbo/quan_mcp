"""Closed-loop persistence tests (issues #2, #3).

SqlPredictionRepository round-trips Forecast/NoForecast with full provenance;
SqlPaperLedger reconciles fills to forecasts; SqlJobStore survives a
simulated restart (new instance, same engine).
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.inference.service import Forecast, NoForecast, NoForecastReason
from packages.persistence.repositories import (
    _metadata, admin_job_t, model_prediction_t, paper_fill_t, paper_order_t,
    SqlJobStore, SqlPaperLedger, SqlPredictionRepository,
)

IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")
AS_OF = datetime(2026, 7, 16, 22, tzinfo=timezone.utc)


def _engine():
    eng = sa.create_engine("sqlite:///:memory:")
    _metadata.create_all(eng, tables=[model_prediction_t, paper_order_t,
                                      paper_fill_t, admin_job_t])
    return eng


def test_prediction_forecast_roundtrip_with_provenance():
    eng = _engine()
    repo = SqlPredictionRepository(eng)
    fc = Forecast(
        IID, AS_OF, 5, 0.5, "m", "v", "hash",
        data_version="yfinance.v1", calendar_version="us.v0",
        rule_version="us.v0",
    )
    pid = repo.record(fc, trace_id="t1")
    row = repo.get(pid)
    assert row["instrument_id"] == "US.NASDAQ.EQUITY.AAPL"
    assert float(row["score"]) == 0.5
    assert row["model_id"] == "m" and row["model_version"] == "v"
    assert row["data_version"] == "yfinance.v1"   # §38 留痕
    assert row["calendar_version"] == "us.v0"
    assert row["rule_version"] == "us.v0"
    assert row["trace_id"] == "t1"
    assert row["no_forecast_reason"] is None


def test_prediction_noforecast_roundtrip():
    eng = _engine()
    repo = SqlPredictionRepository(eng)
    nf = NoForecast(IID, AS_OF, NoForecastReason.NO_PRODUCTION_MODEL, "none")
    pid = repo.record(nf, trace_id="t2")
    row = repo.get(pid)
    assert row["score"] is None
    assert row["no_forecast_reason"] == "no_production_model"
    assert row["trace_id"] == "t2"


def test_paper_ledger_reconciles_fill_to_forecast():
    eng = _engine()
    ledger = SqlPaperLedger(eng)
    oid = ledger.record_order(
        portfolio_id="p1", instrument_id="US.NASDAQ.EQUITY.AAPL",
        side=1, quantity=10, ref_price=150.0, forecast_id="fc1",
        risk_trace_id="rt1")
    fid = ledger.record_fill(
        order_id=oid, instrument_id="US.NASDAQ.EQUITY.AAPL",
        side=1, quantity=10, fill_price=150.5,
        fill_time_utc=AS_OF, forecast_id="fc1")
    fills = ledger.fills_by_forecast("fc1")
    assert len(fills) == 1
    assert fills[0]["fill_id"] == fid
    assert float(fills[0]["fill_price"]) == 150.5
    assert fills[0]["order_id"] == oid  # reconcilable to the originating order


def test_job_store_survives_restart():
    eng = _engine()
    store = SqlJobStore(eng)
    jid = store.create(kind="ingestion", payload_json='{"x":1}')
    assert store.get(jid)["status"] == "QUEUED"
    store.update(jid, status="SUCCESS", result_json='{"rows":100}')
    assert store.get(jid)["status"] == "SUCCESS"
    # issue #3: simulate restart — new store instance, same DB, job persists
    store2 = SqlJobStore(eng)
    row = store2.get(jid)
    assert row is not None and row["status"] == "SUCCESS"
    assert row["result_json"] == '{"rows":100}'
