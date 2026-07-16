"""Issue #9: target-return feasibility gate.

return_target_evaluate classifies a user's return target and NEVER promises a
return. Monthly 10% (annualized ~214%) -> TARGET_NOT_FEASIBLE; aggressive
targets -> RESEARCH_ONLY; reasonable -> FEASIBLE. Every response carries a
no-guarantee disclaimer.
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


def _load_tools():
    spec = importlib.util.spec_from_file_location("qrt_tools_iss9", _TOOLS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qrt_tools_iss9"] = mod
    spec.loader.exec_module(mod)
    return mod


_tools = _load_tools()
ReadTools = _tools.ReadTools


def _make_tools():
    return ReadTools(
        registry=InMemoryModelRegistry(),
        featureset=FeatureSet(names=("ret_1d",)),
        bar_lookup=lambda _iid, _s, _e: [],
        instrument_lookup=lambda _iid: None,
    )


def test_monthly_10pct_is_infeasible():
    """Monthly 10% compounds to ~214% annual — TARGET_NOT_FEASIBLE."""
    r = _make_tools().return_target_evaluate(
        period="monthly", return_target=0.10)
    assert r["data"]["verdict"] == "TARGET_NOT_FEASIBLE"
    assert r["data"]["annualized_target"] > 1.0


def test_aggressive_annual_is_research_only():
    """Annual 50% -> RESEARCH_ONLY (candidates but no trade)."""
    r = _make_tools().return_target_evaluate(
        period="annual", return_target=0.50)
    assert r["data"]["verdict"] == "RESEARCH_ONLY"


def test_moderate_annual_is_feasible():
    """Annual 15% -> FEASIBLE (proceed, still no promise)."""
    r = _make_tools().return_target_evaluate(
        period="annual", return_target=0.15)
    assert r["data"]["verdict"] == "FEASIBLE"


def test_disclaimer_forbids_guarantee():
    """Every response must carry a no-guarantee disclaimer."""
    for target in (0.10, 0.50, 0.15):
        r = _make_tools().return_target_evaluate(
            period="annual", return_target=target)
        disc = r["data"]["disclaimer"]
        assert "guarantee" in disc.lower() or "guaranteed" in disc.lower()
        assert "not promise" in disc.lower() or "no return promise" in disc.lower() \
            or "not promises" in disc.lower()


def test_unknown_period_rejected():
    r = _make_tools().return_target_evaluate(
        period="weekly", return_target=0.05)
    assert r["error"] is not None
    assert r["error"]["code"] == "UNSUPPORTED_HORIZON"


def test_monthly_compounding_correct():
    """Monthly 5% -> (1.05)^12 - 1 = 0.7958... ~79.6% -> RESEARCH_ONLY."""
    r = _make_tools().return_target_evaluate(
        period="monthly", return_target=0.05)
    assert abs(r["data"]["annualized_target"] - (1.05 ** 12 - 1)) < 1e-9
    assert r["data"]["verdict"] == "RESEARCH_ONLY"
