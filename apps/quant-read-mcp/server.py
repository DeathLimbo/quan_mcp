"""quant-read-mcp server — stdio transport over the 16 read-only tools.

Wraps :class:`apps.quant_read_mcp.tools.ReadTools` as MCP tools via FastMCP so
an Agent (or any MCP client) can call them over stdio. The tool *boundary* is
unchanged — this file only adds the transport layer that was intentionally
left minimal in the skeleton (see tools.py docstring).

Run (from repo root, with the project venv active):
    python apps/quant-read-mcp/server.py

Backends not wired at construction time respond with a stable ``NOT_CONFIGURED``
error (by design, spec §93) — the server still starts and exposes every tool.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from packages.features.featureset import FeatureSet
from packages.models.registry import InMemoryModelRegistry

# The package directory uses a hyphen (quant-read-mcp) which is not a valid
# Python identifier, so we load tools.py by file path — same trick the test
# suite uses (tests/unit/test_mcp_surface.py). The module MUST be registered
# in sys.modules before exec so dataclasses with field()/KW_ONLY can resolve
# ``cls.__module__`` (CPython dataclasses._is_type looks it up there).
_HERE = Path(__file__).resolve().parent
_mod_name = "_qrmc_tools"
_spec = importlib.util.spec_from_file_location(_mod_name, _HERE / "tools.py")
assert _spec and _spec.loader, "could not load tools.py"
_tools_mod = importlib.util.module_from_spec(_spec)
sys.modules[_mod_name] = _tools_mod
_spec.loader.exec_module(_tools_mod)
ReadTools = _tools_mod.ReadTools


def _build_default_tools() -> ReadTools:
    """Construct a ReadTools with in-memory / stub backends.

    Real deployments inject DB-backed callables via ``app.dependency_overrides``
    or by constructing ReadTools with wired callables. The stubs here let the
    server start standalone; unwired tools return ``NOT_CONFIGURED`` (fail-closed).
    """
    reg = InMemoryModelRegistry()
    fs = FeatureSet(names=("ret_1d",))

    def _bars(_iid, _start, _end):
        return []

    def _instr(_iid):
        return None

    return ReadTools(
        registry=reg, featureset=fs,
        bar_lookup=_bars, instrument_lookup=_instr,
    )


_tools = _build_default_tools()

mcp = FastMCP("quant-read-mcp")


@mcp.tool()
def data_get_status() -> dict:
    """Latest event_time / available_at / quality_status per (market, dataset)."""
    return _tools.data_get_status()


@mcp.tool()
def instrument_resolve(query: str, market_hint: str | None = None) -> dict:
    """Resolve a code/name into (possibly ambiguous) InstrumentId candidates."""
    return _tools.instrument_resolve(query, market_hint)


@mcp.tool()
def market_get_status(market: str) -> dict:
    """Trading-session state for a market (open/closed/pre/post/holiday)."""
    return _tools.market_get_status(market)


@mcp.tool()
def fund_get_profile(instrument_id: str) -> dict:
    """Fund/ETF static facts and latest NAV snapshot."""
    return _tools.fund_get_profile(instrument_id)


@mcp.tool()
def equity_get_profile(instrument_id: str) -> dict:
    """Equity static facts, listing info and latest fundamentals."""
    return _tools.equity_get_profile(instrument_id)


@mcp.tool()
def portfolio_get_snapshot(portfolio_id: str, as_of: datetime) -> dict:
    """Portfolio positions, cash and valuation as of a datetime."""
    return _tools.portfolio_get_snapshot(portfolio_id, as_of)


@mcp.tool()
def portfolio_get_exposures(portfolio_id: str, as_of: datetime) -> dict:
    """Exposure breakdown by market / sector / currency."""
    return _tools.portfolio_get_exposures(portfolio_id, as_of)


@mcp.tool()
def model_get_production(market: str, horizon_days: int) -> dict:
    """Return the current PRODUCTION model for (market, horizon)."""
    return _tools.model_get_production(market, horizon_days)


@mcp.tool()
def forecast_run(instrument_id: str, as_of: datetime, horizon_days: int) -> dict:
    """Score one instrument at as_of. Returns NO_FORECAST on missing input."""
    return _tools.forecast_run(instrument_id, as_of, horizon_days)


@mcp.tool()
def screen_run(instrument_ids: list[str], as_of: datetime,
               horizon_days: int, top_k: int = 20) -> dict:
    """Score a universe and return the top-k ranked instruments."""
    return _tools.screen_run(instrument_ids, as_of, horizon_days, top_k)


@mcp.tool()
def portfolio_create_proposal(scores: dict[str, float],
                              max_name_weight: float | None = None) -> dict:
    """Build target weights from a scored universe. No side effects."""
    return _tools.portfolio_create_proposal(scores, max_name_weight)


@mcp.tool()
def risk_evaluate_proposal(instrument_id: str, side: int, quantity: float,
                           ref_price: float, proposed_weight: float) -> dict:
    """Run the 8-layer risk engine and return a RiskProposal."""
    return _tools.risk_evaluate_proposal(
        instrument_id=instrument_id, side=side, quantity=quantity,
        ref_price=ref_price, proposed_weight=proposed_weight,
    )


@mcp.tool()
def risk_run_scenario(instrument_id: str, side: int, quantity: float,
                      ref_price: float, shock_bps: float) -> dict:
    """Apply a scenario shock (bps) and re-evaluate risk."""
    return _tools.risk_run_scenario(
        instrument_id=instrument_id, side=side, quantity=quantity,
        ref_price=ref_price, shock_bps=shock_bps,
    )


@mcp.tool()
def prediction_record(prediction_id: str, explanation: str,
                      confirmed: bool) -> dict:
    """Persist the Agent's explanation of a prediction. Cannot alter model output."""
    return _tools.prediction_record(prediction_id, explanation, confirmed)


@mcp.tool()
def report_get_payload(report_id: str) -> dict:
    """Return the structured payload + markdown for a daily report."""
    return _tools.report_get_payload(report_id)


@mcp.tool()
def evaluation_get_summary(model_id: str, window_id: str) -> dict:
    """Return the four-tier evaluation summary for a (model_id, window)."""
    return _tools.evaluation_get_summary(model_id, window_id)


def main() -> None:
    """Run the MCP server on stdio."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
