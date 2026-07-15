"""Tests for MCP tool surfaces (read + admin), spec §93 / §94."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from packages.audit.record import AuditLog, InMemoryAuditSink
from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import Bar
from packages.features.featureset import FeatureSet
from packages.models.registry import InMemoryModelRegistry


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


ROOT = Path(__file__).resolve().parents[2]
read_mcp = _load("_read_mcp_tools", ROOT / "apps" / "quant-read-mcp" / "tools.py")
admin_mcp = _load("_admin_mcp_tools", ROOT / "apps" / "quant-admin-mcp" / "tools.py")


IID_AAPL = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")
IID_MOUTAI = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")


def _bar(d: date, close: float) -> Bar:
    ts = datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc)
    return Bar(
        instrument_id=IID_AAPL, event_time_utc=ts, market_local_date=d,
        open=Decimal(str(close)), high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)), close=Decimal(str(close)),
        volume=Decimal("1000000"), turnover=None, adj_factor=None,
        available_at_utc=ts, source="test",
        calendar_version="cal-v0", rule_version="rule-v0",
    )


def _read_tools(**overrides):
    reg = InMemoryModelRegistry()
    fs = FeatureSet(names=("ret_1d",))
    bars = [_bar(date(2026, 1, i), 100.0 + i) for i in range(1, 11)]

    def bars_lookup(_iid, _s, _e):
        return bars

    def instr_lookup(iid):
        if iid.canonical() == IID_AAPL.canonical():
            return {"instrument_id": iid.canonical(), "name": "Apple Inc",
                    "currency": "USD"}
        return None

    kwargs = dict(
        registry=reg, featureset=fs,
        bar_lookup=bars_lookup, instrument_lookup=instr_lookup,
    )
    kwargs.update(overrides)
    return read_mcp.ReadTools(**kwargs)


# ---- read MCP tests --------------------------------------------------------

def test_read_manifest_has_16_tools():
    assert len(read_mcp.TOOL_MANIFEST) == 16
    names = {t["name"] for t in read_mcp.TOOL_MANIFEST}
    expected = {
        "data_get_status", "instrument_resolve", "market_get_status",
        "fund_get_profile", "equity_get_profile",
        "portfolio_get_snapshot", "portfolio_get_exposures",
        "model_get_production",
        "forecast_run", "screen_run", "portfolio_create_proposal",
        "risk_evaluate_proposal", "risk_run_scenario",
        "prediction_record", "report_get_payload", "evaluation_get_summary",
    }
    assert names == expected


def test_read_instrument_resolve_fallback_canonical_parse():
    t = _read_tools()
    r = t.instrument_resolve(IID_AAPL.canonical())
    assert r["ok"] is True
    assert r["data"][0]["instrument_id"] == IID_AAPL.canonical()


def test_read_instrument_resolve_unknown():
    t = _read_tools()
    r = t.instrument_resolve("BOGUS")
    assert r["ok"] is False


def test_read_market_status_wired():
    t = _read_tools(market_status=lambda m: {"state": "OPEN"} if m == "US" else None)
    ok_r = t.market_get_status("US")
    assert ok_r["ok"] is True and ok_r["data"]["state"] == "OPEN"
    bad = t.market_get_status("XX")
    assert bad["ok"] is False


def test_read_fund_profile_rejects_equity_id():
    t = _read_tools()
    r = t.fund_get_profile(IID_AAPL.canonical())
    assert r["ok"] is False
    assert r["error"]["code"] == "UNSUPPORTED_ASSET"


def test_read_equity_profile_returns_metadata():
    t = _read_tools()
    r = t.equity_get_profile(IID_AAPL.canonical())
    assert r["ok"] is True
    assert r["data"]["name"] == "Apple Inc"


def test_read_model_get_production_none():
    t = _read_tools()
    r = t.model_get_production("US", 5)
    assert r["ok"] is False
    assert r["error"]["code"] == "MODEL_NOT_AVAILABLE"


def test_read_portfolio_create_proposal_normalises():
    t = _read_tools()
    scores = {IID_AAPL.canonical(): 0.9, IID_MOUTAI.canonical(): 0.8}
    r = t.portfolio_create_proposal(scores, max_name_weight=0.5)
    assert r["ok"] is True
    weights = r["data"]["weights"]
    total = sum(weights.values())
    assert 0.0 < total <= 1.0


def test_read_risk_evaluate_proposal_approved():
    t = _read_tools()
    r = t.risk_evaluate_proposal(
        instrument_id=IID_AAPL.canonical(), side=+1,
        quantity=100, ref_price=150.0, proposed_weight=0.05,
        user_permissions=["trade:US"],
        prev_close=150.0, avg_volume_20d=10_000_000,
        exposure_frac_current=0.02, exposure_frac_limit=0.20,
    )
    assert r["ok"] is True
    assert r["data"]["status"] == "APPROVED"


def test_read_risk_run_scenario_applies_shock():
    t = _read_tools()
    r = t.risk_run_scenario(
        instrument_id=IID_AAPL.canonical(), side=+1,
        quantity=100, ref_price=150.0, shock_bps=5000,
        user_permissions=["trade:US"], prev_close=150.0,
    )
    assert r["ok"] is True
    assert r["data"]["shock_bps"] == 5000
    # 5000bps > default limit 3000 → REJECT
    assert r["data"]["verdict"] == "reject"


def test_read_prediction_record_writes_to_recorder():
    calls: list[tuple[str, str, bool]] = []
    def recorder(pid, expl, conf):
        calls.append((pid, expl, conf))
        return "rec_1"

    t = _read_tools(prediction_recorder=recorder)
    r = t.prediction_record(prediction_id="p1", explanation="test", confirmed=True)
    assert r["ok"] is True
    assert calls == [("p1", "test", True)]


def test_read_report_get_payload_returns_markdown():
    payload = {
        "date": "2026-07-15",
        "forecasts": [{"instrument": "US.NASDAQ.EQUITY.AAPL",
                        "score": 0.7, "horizon_days": 5}],
    }
    t = _read_tools(report_lookup=lambda rid: payload if rid == "r1" else None)
    r = t.report_get_payload("r1")
    assert r["ok"] is True
    assert "markdown" in r["data"]
    assert "2026-07-15" in r["data"]["markdown"]


def test_read_evaluation_get_summary():
    t = _read_tools(evaluation_lookup=lambda m, w: {"ic": 0.05, "brier": 0.22})
    r = t.evaluation_get_summary("m1", "w1")
    assert r["ok"] is True
    assert r["data"]["ic"] == 0.05


# ---- admin MCP tests -------------------------------------------------------

def _admin_tools():
    reg = InMemoryModelRegistry()
    log = AuditLog(InMemoryAuditSink())
    return admin_mcp.AdminTools(registry=reg, audit=log), reg, log


def test_admin_manifest_has_13_tools():
    assert len(admin_mcp.TOOL_MANIFEST) == 13
    names = {t["name"] for t in admin_mcp.TOOL_MANIFEST}
    expected = {
        "ingestion_create_job", "feature_create_job", "dataset_create_snapshot",
        "backtest_create_job", "training_create_job", "job_get_status",
        "model_compare", "model_start_shadow",
        "model_request_promotion", "model_approve_promotion",
        "model_request_rollback", "risk_policy_validate", "audit_query",
    }
    assert names == expected


def test_admin_ingestion_create_job_returns_queued():
    t, _, _ = _admin_tools()
    r = t.ingestion_create_job(market="US", dataset="bars_daily",
                                from_date="2026-01-01", to_date="2026-02-01",
                                actor="admin@acme")
    assert r["ok"] is True
    assert r["data"]["status"] == "QUEUED"
    assert r["data"]["job_type"] == "INGESTION"


def test_admin_job_get_status_unknown():
    t, _, _ = _admin_tools()
    r = t.job_get_status(job_id="job_missing")
    assert r["ok"] is False


def test_admin_dual_control_promotion_requires_second_approver():
    t, _, _ = _admin_tools()
    # 1) Register + shadow
    t.register_model(model_id="m1", version="v1", market="US", horizon_days=5,
                     feature_set_hash="h1", actor="alice")
    t.promote_model(model_id="m1", version="v1", to_state="CANDIDATE", actor="alice")
    _cm = {"ic": 0.10, "net_return": 0.08}
    _bm = {"BuyAndHold": {"ic": 0.02, "net_return": 0.03}}
    t.model_start_shadow(model_id="m1", version="v1", actor="alice",
                          candidate_metrics=_cm, baseline_metrics=_bm)
    # 2) Alice requests promotion
    req = t.model_request_promotion(model_id="m1", version="v1", actor="alice")
    assert req["ok"] is True
    rid = req["data"]["request_id"]
    # 3) Alice cannot approve her own promotion
    self_approve = t.model_approve_promotion(request_id=rid, actor="alice",
                                              approval_id="appr_1")
    assert self_approve["ok"] is False
    assert self_approve["error"]["code"] == "PERMISSION_DENIED" or \
           "approver" in self_approve["error"]["message"].lower()
    # 4) Bob (different actor) approves
    result = t.model_approve_promotion(request_id=rid, actor="bob",
                                        approval_id="appr_1")
    assert result["ok"] is True
    assert result["data"]["state"] == "PRODUCTION"


def test_admin_model_compare():
    t, _, _ = _admin_tools()
    t.register_model(model_id="a", version="1", market="US", horizon_days=5,
                     feature_set_hash="h", actor="alice")
    t.register_model(model_id="b", version="1", market="US", horizon_days=5,
                     feature_set_hash="h", actor="alice")
    r = t.model_compare(a_model_id="a", a_version="1",
                        b_model_id="b", b_version="1")
    assert r["ok"] is True
    assert r["data"]["a"]["state"] == "DRAFT"


def test_admin_risk_policy_validate_flags_bad_input():
    t, _, _ = _admin_tools()
    bad = t.risk_policy_validate(policy={"max_turnover": 2.0})
    assert bad["ok"] is True
    assert bad["data"]["valid"] is False
    good = t.risk_policy_validate(policy={
        "max_single_equity_weight": 0.05, "max_single_etf": 0.10,
        "max_sector": 0.25, "max_turnover": 0.15, "min_cash": 0.10,
    })
    assert good["data"]["valid"] is True


def test_admin_audit_query_filters():
    t, _, log = _admin_tools()
    t.register_model(model_id="a", version="1", market="US", horizon_days=5,
                     feature_set_hash="h", actor="alice")
    t.register_model(model_id="b", version="1", market="US", horizon_days=5,
                     feature_set_hash="h", actor="bob")
    r = t.audit_query(actor_id="alice")
    assert r["ok"] is True
    assert r["data"]["total"] == 1
    assert r["data"]["events"][0]["actor_id"] == "alice"
