#!/usr/bin/env python3
"""Ingest A-share fundamentals into fundamental_fact table — task #36.

Pulls financial abstract (EPS / bvps / net_income / operating_cashflow / ROE)
via akshare stock_financial_abstract (sina source) for the 15 A-share leaders,
and upserts into the fundamental_fact table with PIT-safe available_at_utc
(quarterly reports lag 90 days after period end).

Usage (from repo root, venv active, no proxy for akshare):
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        env -u HTTP_PROXY -u HTTPS_PROXY \
        python scripts/ingest_fundamentals.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

# 15 A-share leaders (same as ingest_real_data.py CN_A_EQUITIES)
A_EQUITIES = [
    ("CN.SSE.EQUITY.600519", "600519"),
    ("CN.SZSE.EQUITY.300750", "300750"),
    ("CN.SSE.EQUITY.600036", "600036"),
    ("CN.SSE.EQUITY.601318", "601318"),
    ("CN.SZSE.EQUITY.002594", "002594"),
    ("CN.SSE.EQUITY.601012", "601012"),
    ("CN.SZSE.EQUITY.002415", "002415"),
    ("CN.SZSE.EQUITY.002475", "002475"),
    ("CN.SSE.EQUITY.603259", "603259"),
    ("CN.SSE.EQUITY.600309", "600309"),
    ("CN.SSE.EQUITY.600900", "600900"),
    ("CN.SSE.EQUITY.601088", "601088"),
    ("CN.SZSE.EQUITY.000333", "000333"),
    ("CN.SSE.EQUITY.600690", "600690"),
    ("CN.SZSE.EQUITY.000858", "000858"),
]

# Map akshare 指标 name -> FactName value
INDICATOR_MAP = {
    "基本每股收益": "eps",
    "每股净资产": "book_value_per_share",
    "净利润": "net_income",
    "经营现金流量净额": "operating_cashflow",
    "净资产收益率(ROE)": "roe",
}

# PIT lag: quarterly reports are published 1-4 months after period end.
# Use 90 days as conservative available_at offset.
_PIT_LAG = timedelta(days=90)


def _to_psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def _parse_period(col_name: str) -> date | None:
    """Parse '20251231' -> date(2025,12,31)."""
    try:
        return date(int(col_name[:4]), int(col_name[4:6]), int(col_name[6:8]))
    except (ValueError, IndexError):
        return None


def _parse_value(v) -> Decimal | None:
    """Parse akshare value to Decimal, return None for empty/--."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "--", "NaN", "nan", "None"):
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def main() -> int:
    db = os.getenv("DATABASE_URL")
    if not db:
        print("ERROR: set DATABASE_URL", file=sys.stderr)
        return 1

    import akshare as ak
    url = _to_psycopg_url(db)
    total = 0
    ok, fail = 0, 0

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for iid_str, code in A_EQUITIES:
                try:
                    df = ak.stock_financial_abstract(symbol=code)
                    if df is None or df.empty:
                        print(f"  EMPTY {code}: 0 rows")
                        fail += 1
                        continue
                    # Build {period_date: {fact_name: value}} from the wide table
                    # Columns: [选项, 指标, 20251231, 20250930, ...]
                    period_cols = [c for c in df.columns if c not in ("选项", "指标")]
                    facts_written = 0
                    for _, row in df.iterrows():
                        indicator = str(row["指标"]).strip()
                        fact_name = INDICATOR_MAP.get(indicator)
                        if fact_name is None:
                            continue
                        for col in period_cols:
                            period = _parse_period(col)
                            if period is None:
                                continue
                            val = _parse_value(row[col])
                            if val is None:
                                continue
                            as_of = datetime.combine(period, time(0, 0), tzinfo=timezone.utc)
                            available = as_of + _PIT_LAG
                            cur.execute(
                                "INSERT INTO fundamental_fact"
                                " (instrument_id, fact_name, as_of_utc, available_at_utc,"
                                "  value_num, unit, period_end_local, source)"
                                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                                " ON CONFLICT (instrument_id, fact_name, as_of_utc, source)"
                                " DO UPDATE SET value_num=EXCLUDED.value_num,"
                                "  available_at_utc=EXCLUDED.available_at_utc",
                                (iid_str, fact_name, as_of, available, val,
                                 "CNY" if fact_name != "roe" else "%",
                                 period, "akshare"),
                            )
                            facts_written += 1
                    conn.commit()
                    total += facts_written
                    ok += 1
                    print(f"  OK   {code} ({iid_str}): {facts_written} facts")
                except Exception as e:  # noqa: BLE001
                    conn.rollback()
                    print(f"  FAIL {code}: {e}")
                    fail += 1

    print(f"\ndone: {ok} ok / {fail} fail / {total} total facts")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
