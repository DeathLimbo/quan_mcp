"""Issue #8: new-instrument admission gate.

instrument_admission_check must fail-closed on: unresolvable instrument,
insufficient history, no applicable PRODUCTION model. Only when all hold
is a new fund/equity admitted into the forecast path — preventing a new
name from being force-fed into an existing model family.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from packages.common.instrument_id import Market
from packages.features.featureset import FeatureSet
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState,
)

_TOOLS_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps" / "quant-read-mcp" / "tools.py"
)


def _load_tools():
    spec = importlib.util.spec_from_file_location("qrt_tools_iss8", _TOOLS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qrt_tools_iss8"] = mod
    spec.loader.exec_module(mod)
    return mod


_tools = _load_tools()
ReadTools = _tools.ReadTools


def _registry_with_production(market: Market = Market.CN, horizon: int = 20):
    reg = InMemoryModelRegistry()
    rec = ModelRecord(
        model_id="m1", version="v1", market=market, horizon_days=horizon,
        feature_set_hash="h", state=ModelState.DRAFT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        approved_by=None, approval_id=None,
    )
    reg.register(rec)
    reg.transition("m1", "v1", ModelState.CANDIDATE, actor="t")
    reg.transition("m1", "v1", ModelState.PRODUCTION, actor="t",
                   approval_id="apr", promotion_gate=SimpleNamespace(passed=True))
    return reg


def _make_tools(*, bars_len=250, registry=None):
    return ReadTools(
        registry=registry or _registry_with_production(),
        featureset=FeatureSet(names=("ret_1d",)),
        bar_lookup=lambda _iid, _s, _e: [None] * bars_len,
        instrument_lookup=lambda _iid: None,
    )


def test_admission_unsupported_instrument():
    tools = _make_tools()
    r = tools.instrument_admission_check("not.a.real.instrument")
    assert r["data"]["admitted"] is False
    assert r["data"]["reason"] == "UNSUPPORTED_INSTRUMENT"


def test_admission_insufficient_history():
    tools = _make_tools(bars_len=50)  # below default min_history=200
    r = tools.instrument_admission_check("CN.CN_FUND.FUND.019172")
    assert r["data"]["admitted"] is False
    assert r["data"]["reason"] == "INSUFFICIENT_HISTORY"
    assert r["data"]["bars"] == 50


def test_admission_no_applicable_model():
    # empty registry -> no PRODUCTION model
    tools = _make_tools(registry=InMemoryModelRegistry())
    r = tools.instrument_admission_check("CN.CN_FUND.FUND.019172")
    assert r["data"]["admitted"] is False
    assert r["data"]["reason"] == "NO_APPLICABLE_MODEL"


def test_admission_admitted_when_all_hold():
    tools = _make_tools(bars_len=250)
    r = tools.instrument_admission_check("CN.CN_FUND.FUND.019172")
    assert r["data"]["admitted"] is True
    assert r["data"]["model"] == "m1@v1"
    assert r["data"]["bars"] == 250
    assert r["data"]["market"] == "CN"


def test_admission_custom_min_history():
    tools = _make_tools(bars_len=120)
    # default 200 would reject; lower bar admits
    r = tools.instrument_admission_check(
        "CN.CN_FUND.FUND.019172", min_history=100)
    assert r["data"]["admitted"] is True
