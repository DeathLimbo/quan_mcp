"""Unit tests for model families + baselines (§108) and API routers."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.common.instrument_id import Market
from packages.models.families import (
    BASELINE_REGISTRY,
    BuyAndHold,
    CrossSectionMomentum,
    FAMILIES,
    FamilyId,
    families_for_market,
    get_family,
    make_baseline,
    MovingAverageCross,
    RuleCluster,
)


def test_all_six_families_present():
    ids = {f.id for f in FAMILIES.values()}
    assert ids == set(FamilyId)


def test_family_market_partition():
    assert get_family(FamilyId.CN_FUND_LONG_A).market is Market.CN
    assert get_family(FamilyId.US_EQUITY_CROSS_SECTION_B).market is Market.US
    cn = {f.id for f in families_for_market(Market.CN)}
    assert FamilyId.CN_ETF_SHORT_C in cn
    assert FamilyId.US_EQUITY_CROSS_SECTION_B not in cn


def test_market_regime_is_not_trade_generating():
    assert get_family(FamilyId.MARKET_REGIME).trade_generating is False


def test_baseline_registry_covers_declared_baselines():
    declared = {name for f in FAMILIES.values() for name in f.baselines}
    for name in declared:
        assert name in BASELINE_REGISTRY, name


def test_make_baseline_returns_model_with_predict_one():
    b = make_baseline("BuyAndHold")
    p = b.predict_one({})
    assert p.score == 1.0 and p.horizon_days > 0


def test_ma_cross_uses_features():
    m = MovingAverageCross()
    up = m.predict_one({"ma_5": 2.0, "ma_20": 1.0})
    dn = m.predict_one({"ma_5": 0.5, "ma_20": 1.0})
    assert up.score == 1.0 and dn.score == 0.0


def test_regime_rule_covers_stress_regime():
    r = RuleCluster()
    stress = r.predict_one({"ret_60": -0.20, "vol_60": 0.08})
    assert stress.score == 5.0


def test_xs_momentum_uses_lookback_feature():
    m = CrossSectionMomentum(lookback=20)
    p = m.predict_one({"mom_20": 0.15})
    assert p.score == pytest.approx(0.15)


# --- API routers ------------------------------------------------------

client = TestClient(app)


def test_health_still_ok():
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_instrument_resolve_canonical():
    r = client.get("/v1/instruments/resolve", params={"q": "CN.SSE.EQUITY.600519"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["instrument_id"] == "CN.SSE.EQUITY.600519"
    assert body["data"]["market"] == "CN"


def test_instrument_resolve_rejects_garbage():
    r = client.get("/v1/instruments/resolve", params={"q": "not-an-id"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False


def test_market_status_default_shape():
    r = client.get("/v1/markets/US/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["market"] == "US"
    assert "as_of_utc" in body["data"]


def test_forecast_run_without_service_fails_closed():
    r = client.post("/v1/forecast/run", json={
        "market": "US", "horizon_days": 5, "instruments": []
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    # DataNotReadyError code + reason surfaced in details
    assert body["error"]["code"] == "DATA_NOT_READY"
    assert body["error"]["details"].get("reason") == "NO_INFERENCE_SERVICE"


def test_portfolio_snapshot_without_provider_fails_closed():
    r = client.get("/v1/portfolio/pf1/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "DATA_NOT_READY"
