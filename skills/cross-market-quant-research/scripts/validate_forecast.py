"""Validate a Forecast JSON payload before the Skill returns it to the user.

Exit code 0 = pass; nonzero = fail with a message on stderr. Keeps the Skill
runtime honest: any forecast missing a required field or violating
fail-closed contracts is rejected before it hits the report.

Input contract (stdin JSON or first argv path):

    {
      "as_of_utc": "2026-07-15T02:30:00+00:00",
      "forecasts": [
        {"instrument_id": "CN.SSE.EQUITY.600519",
         "score": 0.6123, "horizon_days": 5,
         "model_id": "CN_EQUITY_CROSS_SECTION_B",
         "model_version": "v1.4.0",
         "feature_hash": "sha256:..."}
      ],
      "no_forecasts": [
        {"instrument_id": "CN.SSE.EQUITY.600000",
         "reason": "MODEL_OOD", "detail": "psi=0.72 > 0.5"}
      ]
    }
"""
from __future__ import annotations

import json
import sys
from typing import Any

_REQ_FC = ("instrument_id", "score", "horizon_days", "model_id",
           "model_version", "feature_hash")
_REQ_NF = ("instrument_id", "reason", "detail")
_VALID_REASONS = {
    "DATA_STALE", "DATA_MISSING", "DATA_QUALITY_FAIL",
    "MODEL_OOD", "MODEL_UNAVAILABLE", "MODEL_LOAD_FAILED",
    "CALENDAR_UNKNOWN", "RULE_UNKNOWN", "MARKET_HALTED",
    "INSTRUMENT_SUSPENDED", "INSTRUMENT_DELISTED",
    "FEATURE_COMPUTATION_ERROR",
}


def _err(path: str, msg: str) -> None:
    print(f"validate_forecast: {path}: {msg}", file=sys.stderr)


def validate(payload: dict[str, Any]) -> int:
    errors = 0
    if "as_of_utc" not in payload or not isinstance(payload["as_of_utc"], str):
        _err(".as_of_utc", "missing or not string"); errors += 1
    fcs = payload.get("forecasts")
    nfs = payload.get("no_forecasts")
    if not isinstance(fcs, list):
        _err(".forecasts", "must be a list (may be empty)"); errors += 1; fcs = []
    if not isinstance(nfs, list):
        _err(".no_forecasts", "must be a list (may be empty)"); errors += 1; nfs = []

    seen_ids: set[str] = set()
    for i, f in enumerate(fcs):
        for k in _REQ_FC:
            if k not in f:
                _err(f".forecasts[{i}].{k}", "missing"); errors += 1
        iid = f.get("instrument_id", "")
        if iid in seen_ids:
            _err(f".forecasts[{i}].instrument_id", f"duplicate {iid}"); errors += 1
        seen_ids.add(iid)
        score = f.get("score")
        if score is not None and not isinstance(score, (int, float)):
            _err(f".forecasts[{i}].score", "must be numeric"); errors += 1
        h = f.get("horizon_days")
        if not (isinstance(h, int) and 0 < h <= 250):
            _err(f".forecasts[{i}].horizon_days", "must be int in (0,250]"); errors += 1

    for i, n in enumerate(nfs):
        for k in _REQ_NF:
            if k not in n:
                _err(f".no_forecasts[{i}].{k}", "missing"); errors += 1
        r = n.get("reason")
        if r not in _VALID_REASONS:
            _err(f".no_forecasts[{i}].reason", f"unknown reason {r!r}"); errors += 1
        if n.get("instrument_id") in seen_ids:
            _err(f".no_forecasts[{i}].instrument_id",
                 f"same id also in forecasts: {n.get('instrument_id')}"); errors += 1
    return errors


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] != "-":
        with open(argv[1], "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        _err(".", "payload must be a JSON object")
        return 1
    n = validate(payload)
    if n > 0:
        print(f"validate_forecast: {n} error(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
