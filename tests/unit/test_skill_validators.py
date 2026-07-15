"""Unit tests for Skill validator scripts."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "cross-market-quant-research" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


VF = _load("validate_forecast")
VR = _load("validate_report")
VT = _load("validate_risk_trace")


def test_validate_forecast_accepts_valid_payload():
    payload = {
        "as_of_utc": "2026-07-15T02:30:00+00:00",
        "forecasts": [{
            "instrument_id": "CN.SSE.EQUITY.600519",
            "score": 0.6, "horizon_days": 5,
            "model_id": "CN_EQUITY_CROSS_SECTION_B",
            "model_version": "v1.0.0",
            "feature_hash": "sha256:abc",
        }],
        "no_forecasts": [],
    }
    assert VF.validate(payload) == 0


def test_validate_forecast_flags_missing_field_and_bad_reason():
    payload = {
        "as_of_utc": "x",
        "forecasts": [{"instrument_id": "x", "score": "not-a-num",
                       "horizon_days": 500, "model_id": "m",
                       "model_version": "v", "feature_hash": "h"}],
        "no_forecasts": [{"instrument_id": "y", "reason": "BOGUS", "detail": ""}],
    }
    assert VF.validate(payload) >= 3


def test_validate_forecast_flags_duplicate_id_across_lists():
    payload = {
        "as_of_utc": "x",
        "forecasts": [{"instrument_id": "A", "score": 0.1, "horizon_days": 5,
                       "model_id": "m", "model_version": "v", "feature_hash": "h"}],
        "no_forecasts": [{"instrument_id": "A", "reason": "MODEL_OOD", "detail": "d"}],
    }
    assert VF.validate(payload) >= 1


def test_validate_report_requires_all_headers():
    good = ("# Daily Report — 2026-07-15\n\n"
            "## Forecasts\n| Instrument | Score | Horizon | Model | Hash |\n"
            "|---|---|---|---|---|\n| X | 0.1 | 5d | m | h |\n"
            "## Portfolio\n- gross = 0.5, cash = 0.5\n")
    assert VR.validate(good) == []
    bad = "just some text with no headers"
    errs = VR.validate(bad)
    assert len(errs) >= 3


def test_validate_report_flags_no_forecast_hint_without_section():
    md = ("# Daily Report\n## Forecasts\n## Portfolio\n"
          "note: some rows were dropped as NO_FORECAST")
    errs = VR.validate(md)
    assert any("NO_FORECAST" in e for e in errs)


def test_validate_risk_trace_happy_path():
    trace = [
        {"layer": "DATA", "verdict": "ACCEPT"},
        {"layer": "MODEL", "verdict": "ACCEPT"},
        {"layer": "PER_ORDER", "verdict": "APPROVED"},
    ]
    assert VT.validate(trace) == []


def test_validate_risk_trace_flags_duplicate_layer_and_post_reject_noise():
    trace = [
        {"layer": "DATA", "verdict": "ACCEPT"},
        {"layer": "DATA", "verdict": "REJECT"},   # duplicate + terminal
        {"layer": "MODEL", "verdict": "ACCEPT"},   # noise after REJECT
        {"layer": "PER_ORDER", "verdict": "REJECTED"},
    ]
    errs = VT.validate(trace)
    assert any("duplicate" in e for e in errs)
    assert any("after first REJECT" in e for e in errs)


def test_validate_risk_trace_adjusted_requires_valid_weights():
    trace = [
        {"layer": "DATA", "verdict": "ACCEPT"},
        {"layer": "PER_ORDER", "verdict": "ADJUSTED",
         "approved_weight": 0.8, "proposed_weight": 0.5},   # backwards
    ]
    errs = VT.validate(trace)
    assert any("ADJUSTED weight invalid" in e for e in errs)


def test_validate_risk_trace_missing_terminal():
    trace = [{"layer": "DATA", "verdict": "ACCEPT"}]
    errs = VT.validate(trace)
    assert any("terminal" in e for e in errs)
