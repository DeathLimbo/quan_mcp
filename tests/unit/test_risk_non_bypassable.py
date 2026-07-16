"""Risk non-bypassable tests (issue #6).

When a portfolio_id is supplied, risk_evaluate_proposal must derive exposure /
currency exposure from the backend, not from caller claims. The Agent cannot
obtain approval by omitting exposure or claiming low exposure.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from packages.features.featureset import FeatureSet
from packages.models.registry import InMemoryModelRegistry

_TOOLS_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps" / "quant-read-mcp" / "tools.py"
)

spec = importlib.util.spec_from_file_location("qrt_tools_nbypass", _TOOLS_PATH)
assert spec and spec.loader
_tools = importlib.util.module_from_spec(spec)
sys.modules["qrt_tools_nbypass"] = _tools
spec.loader.exec_module(_tools)
ReadTools = _tools.ReadTools


def _make(*, port_exp=None, instr=None):
    return ReadTools(
        registry=InMemoryModelRegistry(),
        featureset=FeatureSet(names=("ret_1d",)),
        bar_lookup=lambda *a, **k: [],
        instrument_lookup=instr or (lambda _i: {"currency": "USD"}),
        portfolio_exposures=port_exp,
    )


def test_risk_uses_backend_gross_not_caller_claim():
    # backend says gross=1.5 (> 1.0 limit); Agent passes no exposure fields
    port_exp = lambda _pid, _as_of: {
        "gross_frac": 1.5, "per_name_frac": {}, "per_ccy_frac": {}}
    t = _make(port_exp=port_exp)
    r = t.risk_evaluate_proposal(
        instrument_id="US.NASDAQ.EQUITY.AAPL", side=1, quantity=10,
        ref_price=150.0, proposed_weight=0.05, portfolio_id="p1",
        user_permissions=["trade:US"])
    # issue #6: server-derived gross must reject even though caller claimed nothing
    assert r["data"]["status"] == "REJECTED"
    assert "GROSS_LIMIT" in r["data"]["markdown"]


def test_risk_uses_backend_currency_cap():
    # backend says USD per-currency=0.6 (> 0.4 limit); instrument is USD
    port_exp = lambda _pid, _as_of: {
        "gross_frac": 0.5, "per_name_frac": {}, "per_ccy_frac": {"USD": 0.6}}
    t = _make(port_exp=port_exp, instr=lambda _i: {"currency": "USD"})
    r = t.risk_evaluate_proposal(
        instrument_id="US.NASDAQ.EQUITY.AAPL", side=1, quantity=10,
        ref_price=150.0, proposed_weight=0.05, portfolio_id="p1",
        user_permissions=["trade:US"])
    assert r["data"]["status"] == "REJECTED"
    assert "CCY_LIMIT" in r["data"]["markdown"]


def test_risk_caller_kwargs_used_when_no_portfolio_id():
    # backcompat: without portfolio_id, caller kwargs drive exposure (test-only)
    t = _make()  # no portfolio_exposures backend
    r = t.risk_evaluate_proposal(
        instrument_id="US.NASDAQ.EQUITY.AAPL", side=1, quantity=10,
        ref_price=150.0, proposed_weight=0.05,
        user_permissions=["trade:US"],
        gross_frac_current=0.5, exposure_frac_current=0.05)
    # permission OK, exposure within limits → APPROVED
    assert r["data"]["status"] == "APPROVED"


def test_risk_backend_overrides_caller_claim():
    # Agent claims gross=0.3 but backend says 1.5 → backend wins (non-bypassable)
    port_exp = lambda _pid, _as_of: {
        "gross_frac": 1.5, "per_name_frac": {}, "per_ccy_frac": {}}
    t = _make(port_exp=port_exp)
    r = t.risk_evaluate_proposal(
        instrument_id="US.NASDAQ.EQUITY.AAPL", side=1, quantity=10,
        ref_price=150.0, proposed_weight=0.05, portfolio_id="p1",
        user_permissions=["trade:US"],
        gross_frac_current=0.3)  # Agent claims low — must be ignored
    assert r["data"]["status"] == "REJECTED"
    assert "GROSS_LIMIT" in r["data"]["markdown"]
