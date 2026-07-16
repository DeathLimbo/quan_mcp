"""Auth fail-closed tests (issue #4).

_build_ctx must NOT grant trade permission by default. Missing permission →
_l1_permission REJECTs PERM_DENIED. Only an explicit user_permissions grant
passes layer 1.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from packages.common.instrument_id import parse_instrument_id
from packages.risk.engine import RiskEngine, RiskVerdict

_TOOLS_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps" / "quant-read-mcp" / "tools.py"
)

spec = importlib.util.spec_from_file_location("qrt_tools_auth", _TOOLS_PATH)
assert spec and spec.loader
_tools = importlib.util.module_from_spec(spec)
sys.modules["qrt_tools_auth"] = _tools
spec.loader.exec_module(_tools)
_build_ctx = _tools._build_ctx

IID = parse_instrument_id("US.NASDAQ.EQUITY.AAPL")


def test_no_permission_rejects_perm_denied():
    # issue #4: omitting user_permissions must NOT default to trade:{market}
    ctx = _build_ctx(IID, side=1, quantity=100, ref_price=150.0, kwargs={})
    trace = RiskEngine().evaluate(ctx)
    assert RiskEngine().final_verdict(trace) is RiskVerdict.REJECT
    assert trace[0].code == "PERM_DENIED", \
        f"expected PERM_DENIED, got {trace[0].code}"


def test_explicit_permission_passes_layer1():
    ctx = _build_ctx(IID, side=1, quantity=100, ref_price=150.0,
                     kwargs={"user_permissions": ["trade:US"]})
    trace = RiskEngine().evaluate(ctx)
    # layer 1 (permission) must accept; later layers may still reject/review
    assert trace[0].verdict is RiskVerdict.ACCEPT
    assert trace[0].code == "OK"


def test_wrong_market_permission_rejects():
    # a CN permission must not authorize a US trade
    ctx = _build_ctx(IID, side=1, quantity=100, ref_price=150.0,
                     kwargs={"user_permissions": ["trade:CN"]})
    trace = RiskEngine().evaluate(ctx)
    assert RiskEngine().final_verdict(trace) is RiskVerdict.REJECT
    assert trace[0].code == "PERM_DENIED"
