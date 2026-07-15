"""Turn a user-supplied ticker into the canonical InstrumentId string.

Usage (from the Skill runtime):
    $ python scripts/canonical_id.py 600519
    CN.SSE.EQUITY.600519

The mapping is deterministic and covers the common cases. Ambiguous inputs
print candidates and exit 2 so the agent asks the user to disambiguate.
"""
from __future__ import annotations

import sys


def guess(ticker: str) -> list[str]:
    t = ticker.strip().upper()
    if not t:
        return []
    # US alphabetic tickers
    if t.isalpha():
        return [f"US.NASDAQ.EQUITY.{t}", f"US.NYSE.EQUITY.{t}"]
    # CN numeric tickers
    if t.isdigit():
        if t.startswith(("600", "601", "603", "605", "688", "689")):
            return [f"CN.SSE.EQUITY.{t}"]
        if t.startswith(("000", "001", "002", "003", "300", "301")):
            return [f"CN.SZSE.EQUITY.{t}"]
        if t.startswith(("43", "83", "87", "88")):
            return [f"CN.BSE.EQUITY.{t}"]
        if t.startswith(("51", "58", "56", "159", "588")):
            return [f"CN.SSE.ETF.{t}" if t.startswith(("5", "588")) else f"CN.SZSE.ETF.{t}"]
        # 6-digit fund codes (open-end) usually 0/1
        return [f"CN.CN_FUND.FUND.{t}"]
    return []


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: canonical_id.py <ticker>", file=sys.stderr)
        return 2
    cands = guess(argv[1])
    if not cands:
        print("no guess; please provide full canonical id", file=sys.stderr)
        return 2
    if len(cands) == 1:
        print(cands[0])
        return 0
    print("ambiguous; candidates:", file=sys.stderr)
    for c in cands:
        print(f"  {c}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
