"""Golden dataset fixtures loader.

Fixtures are stored as JSONL under ``tests/fixtures/golden/{market}/{scenario}.jsonl``.
Each line is a JSON object; the ``type`` field selects the decoder.

Types:
- ``bar``        : bar record { instrument_id, date, o, h, l, c, v, halted?, closed_early? }
- ``action``     : corporate action { instrument_id, ex_date, kind, ratio? , dps? }
- ``order``      : test order intent { instrument_id, side, qty, submit_date, submit_hour? }

Fixtures deliberately stay tiny and human-readable — they cover the spec §115
scenario matrix (halt / limit / DST / early close / split / dividend / delisting
/ fund cutoff), not full historical replay.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).parent / "golden"


def load_scenario(market: str, scenario: str) -> list[dict]:
    p = _ROOT / market / f"{scenario}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"golden fixture not found: {p}")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def as_date(s: str) -> date:
    return date.fromisoformat(s)
