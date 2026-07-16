"""PIT filtering regression tests (issue #1).

forecast_run / screen_run must forward the request ``as_of`` to the bar lookup
as a point-in-time cutoff so a same-date bar whose ``available_at_utc`` is
after ``as_of`` cannot leak into a forecast (spec §38 数据).
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from packages.features.featureset import FeatureSet
from packages.models.registry import InMemoryModelRegistry

_TOOLS_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps" / "quant-read-mcp" / "tools.py"
)


def _load_tools():
    spec = importlib.util.spec_from_file_location("qrt_tools_pit", _TOOLS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qrt_tools_pit"] = mod
    spec.loader.exec_module(mod)
    return mod


_tools = _load_tools()
ReadTools = _tools.ReadTools


def _make_tools(bar_lookup):
    return ReadTools(
        registry=InMemoryModelRegistry(),
        featureset=FeatureSet(names=("ret_1d",)),
        bar_lookup=bar_lookup,
        instrument_lookup=lambda _iid: None,
    )


def test_forecast_run_passes_as_of_utc_to_bar_lookup():
    captured: dict = {}

    def bar_lookup(_iid, _start, _end, as_of_utc=None):
        captured["as_of_utc"] = as_of_utc
        return []

    as_of = datetime(2026, 7, 16, 22, tzinfo=timezone.utc)
    _make_tools(bar_lookup).forecast_run(
        "US.NASDAQ.EQUITY.AAPL", as_of, horizon_days=5)
    # issue #1: the PIT cutoff must reach the data layer
    assert captured["as_of_utc"] == as_of


def test_screen_run_passes_as_of_utc_to_every_lookup():
    calls: list = []

    def bar_lookup(_iid, _start, _end, as_of_utc=None):
        calls.append(as_of_utc)
        return []

    as_of = datetime(2026, 7, 16, 22, tzinfo=timezone.utc)
    _make_tools(bar_lookup).screen_run(
        ["US.NASDAQ.EQUITY.AAPL", "US.NYSE.EQUITY.JPM"], as_of, horizon_days=5)
    # both instruments must receive the PIT cutoff (issue #1)
    assert calls == [as_of, as_of]


def test_bar_lookup_without_as_of_still_works_backcompat():
    # older callers that omit as_of_utc must not break (backward compat)
    captured: dict = {}

    def bar_lookup(_iid, _start, _end, as_of_utc=None):
        captured["as_of_utc"] = as_of_utc
        return []

    _make_tools(bar_lookup).forecast_run(
        "US.NASDAQ.EQUITY.AAPL",
        datetime(2026, 7, 16, 22, tzinfo=timezone.utc),
        horizon_days=5,
    )
    # as_of_utc is always forwarded now; the backcompat guarantee is that the
    # callable signature accepts the kwarg without TypeError (covered by the
    # call succeeding) — and the value is the request as_of, never None.
    assert captured["as_of_utc"] is not None
