#!/usr/bin/env python3
"""Ingest real market data into Postgres — task #26.

Pulls daily bars / fund NAVs via the wired adapters (akshare for CN, yfinance
for US) and upserts into the ``instruments`` / ``market_bar`` tables. Replaces
the synthetic seed from db_backends.seed_sample_data with real history.

Usage (from repo root, venv active):
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        python scripts/ingest_real_data.py --days 180

A failed pull for one instrument is logged and skipped — the run continues
with the rest of the universe.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

# Ensure packages/ importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

from packages.common.instrument_id import Market, parse_instrument_id  # noqa: E402

# ---- universe -------------------------------------------------------------

UNIVERSE: list[dict] = [
    # 华尔街之狼 9 只 QDII 持仓基金 (CN, FUND)
    {"iid": "CN.CN_FUND.FUND.019172", "name_local": "摩根纳斯达克100指数(QDII)人民币A", "name_en": "JPM Nasdaq-100 QDII RMB A", "currency": "CNY"},
    {"iid": "CN.CN_FUND.FUND.270042", "name_local": "广发纳指100ETF联接(QDII)人民币A", "name_en": "GF Nasdaq-100 ETF Feeder QDII RMB A", "currency": "CNY"},
    {"iid": "CN.CN_FUND.FUND.160213", "name_local": "国泰纳斯达克100指数(QDII)", "name_en": "GT Nasdaq-100 Index QDII", "currency": "CNY"},
    {"iid": "CN.CN_FUND.FUND.017436", "name_local": "华宝纳斯达克精选股票发起式(QDII)A", "name_en": "HB Nasdaq Select Equity QDII A", "currency": "CNY"},
    {"iid": "CN.CN_FUND.FUND.000055", "name_local": "广发纳指100ETF联接(QDII)美元A", "name_en": "GF Nasdaq-100 ETF Feeder QDII USD A", "currency": "USD"},
    {"iid": "CN.CN_FUND.FUND.025208", "name_local": "永赢先锋半导体智选混合发起A", "name_en": "Maxiem Pioneer Semiconductor A", "currency": "CNY"},
    {"iid": "CN.CN_FUND.FUND.007721", "name_local": "天弘标普500发起(QDII-FOF)A", "name_en": "TH S&P500 QDII-FOF A", "currency": "CNY"},
    {"iid": "CN.CN_FUND.FUND.018344", "name_local": "华夏中证机器人ETF联接基金A类", "name_en": "ChinaAMC CSI Robotics ETF Feeder A", "currency": "CNY"},
    # US benchmarks / reference equities
    {"iid": "US.NASDAQ.EQUITY.AAPL", "name_local": "苹果", "name_en": "Apple Inc", "currency": "USD"},
    {"iid": "US.NASDAQ.EQUITY.MSFT", "name_local": "微软", "name_en": "Microsoft Corp", "currency": "USD"},
    {"iid": "US.NASDAQ.ETF.QQQ", "name_local": "QQQ", "name_en": "Invesco QQQ Trust", "currency": "USD"},
    {"iid": "US.ARCA.ETF.SPY", "name_local": "SPY", "name_en": "SPDR S&P 500 ETF", "currency": "USD"},
]


def _to_psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def _upsert_instrument(cur, iid, meta):
    cur.execute(
        "INSERT INTO instruments"
        " (instrument_id, market, venue, asset_type, symbol,"
        "  name_local, name_en, currency, lot_size, first_trade_date,"
        "  last_trade_date, status, ingested_at_utc, calendar_version, rule_version)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE', now(),'v0','v0')"
        " ON CONFLICT (instrument_id) DO UPDATE SET"
        "  name_local=EXCLUDED.name_local, name_en=EXCLUDED.name_en",
        (iid.canonical(), iid.market.value, iid.venue.value, iid.asset_type.value,
         iid.symbol, meta["name_local"], meta["name_en"], meta["currency"],
         100 if iid.asset_type.value == "EQUITY" else 1,
         date.today() - timedelta(days=365), None),
    )


def _upsert_bars(cur, iid, bars):
    rows_written = 0
    for b in bars:
        cur.execute(
            "INSERT INTO market_bar"
            " (instrument_id, market_local_date, event_time_utc,"
            "  open, high, low, close, volume, turnover, adj_factor,"
            "  available_at_utc, source, calendar_version, rule_version,"
            "  source_version, license_tag, quality_status)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (instrument_id, market_local_date, source)"
            " DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,"
            "  low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume",
            (b.instrument_id.canonical(), b.market_local_date, b.event_time_utc,
             b.open, b.high, b.low, b.close, b.volume,
             b.turnover, b.adj_factor, b.available_at_utc,
             b.source, b.calendar_version, b.rule_version,
             b.source_version, b.license_tag, b.quality_status),
        )
        rows_written += 1
    return rows_written


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest real market data into Postgres")
    ap.add_argument("--days", type=int, default=180, help="history window in days")
    ap.add_argument("--db", default=os.getenv("DATABASE_URL"), help="database URL")
    args = ap.parse_args()
    if not args.db:
        print("ERROR: set DATABASE_URL or pass --db", file=sys.stderr)
        return 1

    from packages.data_sources.adapters.akshare_adapter import AkshareAdapter
    from packages.data_sources.adapters.yfinance_adapter import YfinanceAdapter

    ak = AkshareAdapter()
    yf = YfinanceAdapter()
    adapter_map = {Market.CN: ak, Market.US: yf}

    end = date.today()
    start = end - timedelta(days=args.days)
    url = _to_psycopg_url(args.db)

    total_bars = 0
    ok, fail = 0, 0
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for meta in UNIVERSE:
                iid = parse_instrument_id(meta["iid"])
                adapter = adapter_map.get(iid.market)
                if adapter is None:
                    print(f"  SKIP {meta['iid']}: no adapter for {iid.market}")
                    fail += 1
                    continue
                try:
                    bars = list(adapter.fetch_bars_daily(iid, start, end, adjust="forward"))
                    if not bars:
                        print(f"  EMPTY {meta['iid']}: 0 bars returned")
                        fail += 1
                        continue
                    _upsert_instrument(cur, iid, meta)
                    n = _upsert_bars(cur, iid, bars)
                    conn.commit()
                    total_bars += n
                    ok += 1
                    print(f"  OK   {meta['iid']}: {n} bars")
                except Exception as e:  # noqa: BLE001
                    conn.rollback()
                    print(f"  FAIL {meta['iid']}: {e}")
                    fail += 1

    print(f"\ndone: {ok} ok / {fail} fail / {total_bars} total bars over {args.days}d")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
