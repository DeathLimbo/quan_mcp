"""DB-backed read backends for quant-read-mcp.

Replaces the in-memory stubs (bar_lookup returns [], instrument_lookup returns
None) with callables that query the Postgres ``instruments`` / ``market_bar``
tables populated by the data-ingestion pipeline.

Usage in server.py:
    if os.getenv("DATABASE_URL"):
        backends = make_db_backends(os.environ["DATABASE_URL"])
        ReadTools(registry=..., featureset=..., **backends)
    else:
        # fall back to in-memory stubs (fail-closed)

``make_db_backends`` returns a dict with the keys ReadTools accepts:
``bar_lookup``, ``instrument_lookup``, ``instrument_resolver``, ``data_status``.

Run ``python apps/quant-read-mcp/db_backends.py`` to seed sample data
(the 9 华尔街之狼 holding funds + AAPL/MSFT/QQQ/SPY, 30 trading days of OHLCV).
"""
from __future__ import annotations

import os
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue, parse_instrument_id,
)
from packages.data_sources.contracts import Bar

# ---- canonical instrument ids for the 华尔街之狼 portfolio + US benchmarks ---
# Format: {market}.{venue}.{asset_type}.{symbol}
SAMPLE_INSTRUMENTS: list[dict[str, Any]] = [
    # 华尔街之狼 9 只 QDII 持仓基金 (CN market, FUND asset, CN_FUND venue)
    {"instrument_id": "CN.CN_FUND.FUND.019172", "name_local": "摩根纳斯达克100指数(QDII)人民币A",
     "name_en": "JPM Nasdaq-100 QDII RMB A", "currency": "CNY"},
    {"instrument_id": "CN.CN_FUND.FUND.270042", "name_local": "广发纳指100ETF联接(QDII)人民币A",
     "name_en": "GF Nasdaq-100 ETF Feeder QDII RMB A", "currency": "CNY"},
    {"instrument_id": "CN.CN_FUND.FUND.160213", "name_local": "国泰纳斯达克100指数(QDII)",
     "name_en": "GT Nasdaq-100 Index QDII", "currency": "CNY"},
    {"instrument_id": "CN.CN_FUND.FUND.017436", "name_local": "华宝纳斯达克精选股票发起式(QDII)A",
     "name_en": "HB Nasdaq Select Equity QDII A", "currency": "CNY"},
    {"instrument_id": "CN.CN_FUND.FUND.000055", "name_local": "广发纳指100ETF联接(QDII)美元A",
     "name_en": "GF Nasdaq-100 ETF Feeder QDII USD A", "currency": "USD"},
    {"instrument_id": "CN.CN_FUND.FUND.025208", "name_local": "永赢先锋半导体智选混合发起A",
     "name_en": "Maxiem Pioneer Semiconductor A", "currency": "CNY"},
    {"instrument_id": "CN.CN_FUND.FUND.007721", "name_local": "天弘标普500发起(QDII-FOF)A",
     "name_en": "TH S&P500 QDII-FOF A", "currency": "CNY"},
    {"instrument_id": "CN.CN_FUND.FUND.018344", "name_local": "华夏中证机器人ETF联接基金A类",
     "name_en": "ChinaAMC CSI Robotics ETF Feeder A", "currency": "CNY"},
    # US benchmarks / reference equities
    {"instrument_id": "US.NASDAQ.EQUITY.AAPL", "name_local": "苹果",
     "name_en": "Apple Inc", "currency": "USD"},
    {"instrument_id": "US.NASDAQ.EQUITY.MSFT", "name_local": "微软",
     "name_en": "Microsoft Corp", "currency": "USD"},
    {"instrument_id": "US.NASDAQ.ETF.QQQ", "name_local": "QQQ",
     "name_en": "Invesco QQQ Trust", "currency": "USD"},
    {"instrument_id": "US.ARCA.ETF.SPY", "name_local": "SPY",
     "name_en": "SPDR S&P 500 ETF", "currency": "USD"},
]


def _to_psycopg_url(url: str) -> str:
    """``postgresql+psycopg://...`` (SQLAlchemy) -> ``postgresql://...`` (psycopg)."""
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def make_db_backends(database_url: str) -> dict[str, Any]:
    """Build DB-backed callables for ReadTools.

    Returns a dict with ``bar_lookup``, ``instrument_lookup``,
    ``instrument_resolver``, ``data_status`` — the keys ReadTools.__init__
    accepts. Connection strings use the psycopg-native format; the SQLAlchemy
    ``postgresql+psycopg://`` prefix is stripped automatically.
    """
    url = _to_psycopg_url(database_url)

    def instrument_lookup(iid: InstrumentId) -> dict[str, Any] | None:
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT instrument_id, market, venue, asset_type, symbol,"
                    " name_local, name_en, currency, lot_size, status"
                    " FROM instruments WHERE instrument_id = %s",
                    (iid.canonical(),),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {
            "instrument_id": row["instrument_id"],
            "name": row["name_en"] or row["name_local"] or row["symbol"],
            "name_local": row["name_local"],
            "name_en": row["name_en"],
            "symbol": row["symbol"],
            "currency": row["currency"],
            "lot_size": row["lot_size"],
            "status": row["status"],
        }

    def bar_lookup(iid: InstrumentId, start: date, end: date) -> list[Bar]:
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT instrument_id, market_local_date, event_time_utc,"
                    " open, high, low, close, volume, turnover, adj_factor,"
                    " available_at_utc, source, calendar_version, rule_version,"
                    " source_version, license_tag, quality_status"
                    " FROM market_bar"
                    " WHERE instrument_id = %s"
                    "   AND market_local_date BETWEEN %s AND %s"
                    " ORDER BY market_local_date",
                    (iid.canonical(), start, end),
                )
                rows = cur.fetchall()
        bars: list[Bar] = []
        for r in rows:
            bars.append(Bar(
                instrument_id=iid,
                event_time_utc=r["event_time_utc"],
                market_local_date=r["market_local_date"],
                open=Decimal(r["open"]),
                high=Decimal(r["high"]),
                low=Decimal(r["low"]),
                close=Decimal(r["close"]),
                volume=Decimal(r["volume"]),
                turnover=Decimal(r["turnover"]) if r["turnover"] is not None else None,
                adj_factor=Decimal(r["adj_factor"]) if r["adj_factor"] is not None else None,
                available_at_utc=r["available_at_utc"],
                source=r["source"],
                calendar_version=r["calendar_version"],
                rule_version=r["rule_version"],
                source_version=r["source_version"],
                license_tag=r["license_tag"],
                quality_status=r["quality_status"],
            ))
        return bars

    def instrument_resolver(query: str, market_hint: str | None = None) -> list[dict[str, Any]]:
        """Resolve a code/name into InstrumentId candidates.

        Tries exact symbol match first, then canonical-id parse, then
        name_local/name_en ILIKE. Returns ranked candidates.
        """
        q = query.strip()
        candidates: list[dict[str, Any]] = []
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                # 1) exact symbol
                cur.execute(
                    "SELECT instrument_id FROM instruments WHERE symbol = %s"
                    + (" AND market = %s" if market_hint else ""),
                    (q.upper(),) + ((market_hint,) if market_hint else ()),
                )
                for r in cur.fetchall():
                    candidates.append({"instrument_id": r["instrument_id"],
                                       "confidence": 1.0, "reason": "exact_symbol"})
                if not candidates:
                    # 2) canonical parse
                    try:
                        iid = parse_instrument_id(q)
                        cur.execute(
                            "SELECT 1 FROM instruments WHERE instrument_id = %s",
                            (iid.canonical(),),
                        )
                        if cur.fetchone():
                            candidates.append({"instrument_id": iid.canonical(),
                                               "confidence": 1.0, "reason": "canonical"})
                    except Exception:
                        pass
                if not candidates:
                    # 3) name ILIKE
                    cur.execute(
                        "SELECT instrument_id FROM instruments"
                        " WHERE name_local ILIKE %s OR name_en ILIKE %s"
                        + (" AND market = %s" if market_hint else ""),
                        (f"%{q}%", f"%{q}%") + ((market_hint,) if market_hint else ()),
                    )
                    for r in cur.fetchall():
                        candidates.append({"instrument_id": r["instrument_id"],
                                           "confidence": 0.7, "reason": "name_match"})
        return candidates

    def data_status() -> list[dict[str, Any]]:
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT instrument_id, MAX(event_time_utc) AS latest_event_time,"
                    " MAX(available_at_utc) AS latest_available_at,"
                    " COUNT(*) AS bar_count"
                    " FROM market_bar GROUP BY instrument_id ORDER BY instrument_id",
                )
                return [
                    {"instrument_id": r["instrument_id"],
                     "latest_event_time": r["latest_event_time"].isoformat() if r["latest_event_time"] else None,
                     "latest_available_at": r["latest_available_at"].isoformat() if r["latest_available_at"] else None,
                     "bar_count": r["bar_count"]}
                    for r in cur.fetchall()
                ]

    return {
        "bar_lookup": bar_lookup,
        "instrument_lookup": instrument_lookup,
        "instrument_resolver": instrument_resolver,
        "data_status": data_status,
    }


def seed_sample_data(database_url: str, *, days: int = 30) -> None:
    """Seed instruments + synthetic OHLCV bars for the sample universe.

    Idempotent: uses INSERT ... ON CONFLICT DO NOTHING for instruments and
    ON CONFLICT DO UPDATE for bars (re-seed overwrites prices).
    """
    url = _to_psycopg_url(database_url)
    rng = random.Random(42)  # deterministic for reproducibility
    today = date.today()

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            # 1) instruments
            for ins in SAMPLE_INSTRUMENTS:
                iid = parse_instrument_id(ins["instrument_id"])
                cur.execute(
                    "INSERT INTO instruments"
                    " (instrument_id, market, venue, asset_type, symbol,"
                    "  name_local, name_en, currency, lot_size, first_trade_date,"
                    "  last_trade_date, status, ingested_at_utc, calendar_version, rule_version)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE', now(),'v0','v0')"
                    " ON CONFLICT (instrument_id) DO NOTHING",
                    (iid.canonical(), iid.market.value, iid.venue.value,
                     iid.asset_type.value, iid.symbol,
                     ins["name_local"], ins["name_en"], ins["currency"],
                     100 if iid.asset_type == AssetType.EQUITY else 1,
                     today - timedelta(days=365), None),
                )

            # 2) market_bar — 30 trading days of synthetic OHLCV per instrument
            # Base price per asset class for realistic ranges.
            base_price = {
                AssetType.EQUITY: 150.0,
                AssetType.ETF: 400.0,
                AssetType.FUND: 1.5,  # QDII fund NAV ~1.x
            }
            seeded = 0
            for ins in SAMPLE_INSTRUMENTS:
                iid = parse_instrument_id(ins["instrument_id"])
                price = base_price.get(iid.asset_type, 100.0)
                for i in range(days):
                    d = today - timedelta(days=days - 1 - i)
                    # skip weekends for equities/ETFs; funds report daily
                    if iid.asset_type in (AssetType.EQUITY, AssetType.ETF) and d.weekday() >= 5:
                        continue
                    # random walk
                    pct = rng.uniform(-0.025, 0.028)
                    price = max(0.01, price * (1 + pct))
                    o = round(price * rng.uniform(0.998, 1.002), 4)
                    c = round(price, 4)
                    h = round(max(o, c) * rng.uniform(1.0, 1.015), 4)
                    lo = round(min(o, c) * rng.uniform(0.985, 1.0), 4)
                    vol = Decimal(str(int(rng.uniform(5e5, 5e6))))
                    ts = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
                    ts = ts.replace(hour=21 if iid.market == Market.US else 15)
                    cur.execute(
                        "INSERT INTO market_bar"
                        " (instrument_id, market_local_date, event_time_utc,"
                        "  open, high, low, close, volume, turnover, adj_factor,"
                        "  available_at_utc, source, calendar_version, rule_version,"
                        "  source_version, license_tag, quality_status)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,1.0,%s,'seed','cal-v0','rule-v0','seed-v1','INTERNAL_RESEARCH','NORMAL')"
                        " ON CONFLICT (instrument_id, market_local_date, source)"
                        " DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,"
                        " low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume",
                        (iid.canonical(), d, ts, Decimal(str(o)), Decimal(str(h)),
                         Decimal(str(lo)), Decimal(str(c)), vol, ts),
                    )
                    seeded += 1
            conn.commit()
    print(f"seeded {len(SAMPLE_INSTRUMENTS)} instruments + {seeded} bars ({days} days)")


if __name__ == "__main__":  # pragma: no cover
    db = os.getenv("DATABASE_URL")
    if not db:
        raise SystemExit("set DATABASE_URL to seed (e.g. postgresql+psycopg://quant:quant@localhost:5432/quant)")
    seed_sample_data(db)
    # quick verify
    backends = make_db_backends(db)
    iid = parse_instrument_id("US.NASDAQ.EQUITY.AAPL")
    print("AAPL profile ->", backends["instrument_lookup"](iid))
    bars = backends["bar_lookup"](iid, date.today() - timedelta(days=60), date.today())
    print(f"AAPL bars -> {len(bars)} rows, last close {bars[-1].close if bars else 'n/a'}")
