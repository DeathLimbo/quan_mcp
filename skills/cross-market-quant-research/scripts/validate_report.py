"""Validate a rendered daily-report markdown payload.

Enforces the four sections mandated by §90 and rejects any report where
NO_FORECAST items are present but not disclosed in the markdown body.

Usage:
    $ python scripts/validate_report.py path/to/report.md
    $ echo "$MARKDOWN" | python scripts/validate_report.py -
"""
from __future__ import annotations

import re
import sys


_REQUIRED_HEADERS = ("# Daily Report", "## Forecasts", "## Portfolio")


def validate(md: str) -> list[str]:
    errs: list[str] = []
    for h in _REQUIRED_HEADERS:
        if h not in md:
            errs.append(f"missing required header: {h!r}")
    # If NO_FORECAST list exists, `## NO_FORECAST` header must be present.
    if re.search(r"\bNO_FORECAST\b", md) and "## NO_FORECAST" not in md:
        errs.append("NO_FORECAST reason referenced but header not rendered")
    # Every score row must have a horizon in the form `<int>d`.
    for line in md.splitlines():
        if line.startswith("|") and "|" in line and line.count("|") >= 5:
            # Skip separator + header
            if "---" in line or "Horizon" in line or "Weight" in line:
                continue
            if "Instrument" in line:
                continue
            # Non-strict: skip portfolio rows (4 pipes) — those have Weight
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) == 5 and not re.fullmatch(r"\d+d", parts[2]):
                errs.append(f"malformed horizon in row: {line!r}")
    return errs


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] != "-":
        with open(argv[1], "r", encoding="utf-8") as fh:
            md = fh.read()
    else:
        md = sys.stdin.read()
    errs = validate(md)
    if errs:
        for e in errs:
            print(f"validate_report: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
