"""Validate a RiskTrace (list of RiskDecision dicts) before display.

Rules enforced:
- Every layer must appear at most once.
- First REJECT short-circuits: any decisions after the first REJECT are a
  contract violation and rejected as noise.
- ADJUSTED must include an ``approved_weight`` in [0,1) that is *strictly*
  less than the ``proposed_weight``.
- The trace must terminate with one of APPROVED / ADJUSTED / REJECTED as
  the last non-review verdict; a bare list of ACCEPTs is not a full trace.

Usage:
    $ python scripts/validate_risk_trace.py trace.json
"""
from __future__ import annotations

import json
import sys
from typing import Any

_VALID_LAYERS = (
    "DATA", "MODEL", "INSTRUMENT", "PER_ORDER",
    "CONCENTRATION", "MARKET_CCY", "DRAWDOWN", "PERMISSION",
)
_VALID_VERDICTS = {"ACCEPT", "REJECT", "REVIEW", "APPROVED", "ADJUSTED", "REJECTED"}


def validate(trace: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    seen_layers: set[str] = set()
    rejected = False
    terminal = None
    for i, d in enumerate(trace):
        for k in ("layer", "verdict"):
            if k not in d:
                errs.append(f"[{i}] missing {k}")
                continue
        layer = d.get("layer")
        verdict = d.get("verdict")
        if layer not in _VALID_LAYERS:
            errs.append(f"[{i}] unknown layer: {layer!r}")
        if verdict not in _VALID_VERDICTS:
            errs.append(f"[{i}] unknown verdict: {verdict!r}")
        if layer in seen_layers:
            errs.append(f"[{i}] duplicate layer: {layer}")
        seen_layers.add(layer)
        if rejected:
            errs.append(f"[{i}] decision after first REJECT is disallowed")
        if verdict == "REJECT":
            rejected = True
        if verdict in ("APPROVED", "ADJUSTED", "REJECTED"):
            terminal = verdict
        if verdict == "ADJUSTED":
            aw = d.get("approved_weight")
            pw = d.get("proposed_weight")
            if aw is None or pw is None:
                errs.append(f"[{i}] ADJUSTED must include approved_weight and proposed_weight")
            elif not (0.0 <= aw < pw <= 1.0):
                errs.append(f"[{i}] ADJUSTED weight invalid: approved={aw} proposed={pw}")
    if terminal is None and trace:
        errs.append("trace missing terminal verdict (APPROVED/ADJUSTED/REJECTED)")
    return errs


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] != "-":
        with open(argv[1], "r", encoding="utf-8") as fh:
            trace = json.load(fh)
    else:
        trace = json.load(sys.stdin)
    if not isinstance(trace, list):
        print("validate_risk_trace: payload must be a JSON list", file=sys.stderr)
        return 1
    errs = validate(trace)
    if errs:
        for e in errs:
            print(f"validate_risk_trace: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
