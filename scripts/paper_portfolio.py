"""Paper portfolio simulator (issue: 2万 CNY, CN+US, MCP-driven, daily settle).

Uses the same ReadTools interface the MCP server exposes (forecast_run /
screen_run / risk_evaluate_proposal), so behavior is identical whether called
via MCP or directly here. State persists to paper_portfolio.json so the
simulation survives across runs.

Usage:
    python scripts/paper_portfolio.py init       # 2万 CNY cash, empty positions
    python scripts/paper_portfolio.py rebalance  # forecast → top-k → buy/sell
    python scripts/paper_portfolio.py settle     # mark-to-market with latest close
    python scripts/paper_portfolio.py status     # print holdings + net value
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, date, timezone
from pathlib import Path

import sqlalchemy as sa

ROOT = Path("/Volumes/Elements/quan_mcp")
sys.path.insert(0, str(ROOT))

from packages.features.featureset import FeatureSet
from packages.persistence.repositories import SqlModelRegistry

PORTFOLIO_FILE = ROOT / "paper_portfolio.json"
INITIAL_CASH_CNY = 20000.0
USDCNY_FALLBACK = 7.25
TOP_K = 8
HORIZON = 20
DB_URL = "postgresql+psycopg://quant:quant@localhost:5432/quant"


# --------------------------------------------------------------------------- #
# load ReadTools (same module the MCP server uses)
# --------------------------------------------------------------------------- #
def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_tools_mod = _load(ROOT / "apps" / "quant-read-mcp" / "tools.py", "pp_tools")
_dbb_mod = _load(ROOT / "apps" / "quant-read-mcp" / "db_backends.py", "pp_dbb")


def _make_tools():
    eng = sa.create_engine(DB_URL, future=True)
    reg = SqlModelRegistry(eng, str(ROOT / "model_store"))
    backends = _dbb_mod.make_db_backends(DB_URL)
    return _tools_mod.ReadTools(
        registry=reg, featureset=FeatureSet(names=("ret_1d",)), **backends), eng


# --------------------------------------------------------------------------- #
# portfolio state
# --------------------------------------------------------------------------- #
def load_pf() -> dict:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return {"cash_cny": INITIAL_CASH_CNY, "positions": {}, "trades": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "net_history": []}


def save_pf(pf: dict) -> None:
    PORTFOLIO_FILE.write_text(json.dumps(pf, indent=2, default=str))


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def universe(eng) -> list[str]:
    """All CN + US instrument_ids with bars."""
    with eng.connect() as c:
        rows = c.execute(sa.text(
            "SELECT DISTINCT instrument_id FROM market_bar "
            "WHERE instrument_id LIKE 'CN.%' OR instrument_id LIKE 'US.%' "
            "ORDER BY instrument_id")).all()
    return [r[0] for r in rows]


def latest_close(eng, iid: str) -> tuple[float, str] | None:
    """Return (close, currency) for the latest bar of iid."""
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT close, instrument_id FROM market_bar WHERE instrument_id=:i "
            "ORDER BY market_local_date DESC LIMIT 1"), {"i": iid}).first()
    if row is None:
        return None
    # currency: US->USD, CN->CNY
    cur = "USD" if iid.startswith("US.") else "CNY"
    return float(row[0]), cur


def usdcny_rate(eng) -> float:
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT close FROM market_bar WHERE instrument_id='US.FX.FX.USDCNY' "
            "ORDER BY market_local_date DESC LIMIT 1")).first()
    return float(row[0]) if row else USDCNY_FALLBACK


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_init():
    pf = {"cash_cny": INITIAL_CASH_CNY, "positions": {}, "trades": [],
          "created_at": datetime.now(timezone.utc).isoformat(),
          "net_history": []}
    save_pf(pf)
    print(f"init: cash=¥{INITIAL_CASH_CNY:.2f}, positions=0")


def cmd_rebalance():
    tools, eng = _make_tools()
    pf = load_pf()
    as_of = datetime(2026, 7, 16, 22, tzinfo=timezone.utc)
    ids = universe(eng)
    print(f"universe: {len(ids)} instruments (CN+US)")
    res = tools.screen_run(ids, as_of, HORIZON, top_k=TOP_K)
    if not res.get("data"):
        print("screen_run failed:", res)
        return
    top = res["data"]["top"]
    skipped = res["data"]["skipped"]
    print(f"screen: {len(top)} ranked, {len(skipped)} skipped (no forecast)")
    if not top:
        print("no forecasts — nothing to buy")
        return

    fx = usdcny_rate(eng)
    print(f"USDCNY={fx}")

    # equal-weight allocation across top_k, in CNY terms
    per_name_cny = pf["cash_cny"] / len(top)
    # sell everything first (full rebalance)
    for iid in list(pf["positions"].keys()):
        pos = pf["positions"].pop(iid)
        close = latest_close(eng, iid)
        if close:
            value_cny = pos["shares"] * close[0] * (fx if close[1] == "USD" else 1.0)
            pf["cash_cny"] += value_cny
            pf["trades"].append({"date": as_of.date().isoformat(), "iid": iid,
                                 "side": "sell", "shares": pos["shares"],
                                 "price": close[0], "currency": close[1],
                                 "value_cny": value_cny})

    # buy top-k
    for item in top:
        iid = item["instrument_id"]
        score = item["score"]
        close = latest_close(eng, iid)
        if not close:
            continue
        price, cur = close
        price_cny = price * (fx if cur == "USD" else 1.0)
        shares = per_name_cny / price_cny if price_cny > 0 else 0
        pf["positions"][iid] = {
            "shares": shares, "entry_price": price, "currency": cur,
            "entry_price_cny": price_cny, "score": score,
            "bought_at": as_of.date().isoformat(),
        }
        pf["cash_cny"] -= shares * price_cny
        pf["trades"].append({"date": as_of.date().isoformat(), "iid": iid,
                             "side": "buy", "shares": shares, "price": price,
                             "currency": cur, "value_cny": shares * price_cny,
                             "score": score})
        print(f"  BUY {iid} shares={shares:.4f} @ {price} {cur} "
              f"(¥{shares*price_cny:.2f}) score={score:.3f}")

    save_pf(pf)
    print(f"rebalance done: {len(pf['positions'])} positions, "
          f"cash=¥{pf['cash_cny']:.2f}")


def cmd_settle():
    eng = sa.create_engine(DB_URL, future=True)
    pf = load_pf()
    fx = usdcny_rate(eng)
    pos_value_cny = 0.0
    print("=== positions ===")
    for iid, pos in pf["positions"].items():
        close = latest_close(eng, iid)
        if not close:
            print(f"  {iid}: no bar")
            continue
        price, cur = close
        price_cny = price * (fx if cur == "USD" else 1.0)
        mv = pos["shares"] * price_cny
        pos_value_cny += mv
        pnl = (price_cny - pos["entry_price_cny"]) * pos["shares"]
        print(f"  {iid}: {pos['shares']:.4f} @ {price} {cur} "
              f"mv=¥{mv:.2f} pnl=¥{pnl:+.2f}")
    net = pf["cash_cny"] + pos_value_cny
    pnl_total = net - INITIAL_CASH_CNY
    print(f"=== cash=¥{pf['cash_cny']:.2f}  positions=¥{pos_value_cny:.2f}  "
          f"net=¥{net:.2f}  PnL=¥{pnl_total:+.2f} ({pnl_total/INITIAL_CASH_CNY:+.2%})")
    pf["net_history"].append({"date": date.today().isoformat(), "net_cny": net,
                              "pnl_cny": pnl_total})
    save_pf(pf)


def cmd_status():
    pf = load_pf()
    print(f"created: {pf['created_at']}")
    print(f"cash: ¥{pf['cash_cny']:.2f}")
    print(f"positions: {len(pf['positions'])}")
    for iid, pos in pf["positions"].items():
        print(f"  {iid}: {pos['shares']:.4f} @ entry {pos['entry_price']} "
              f"{pos['currency']} (score {pos['score']:.3f})")
    print(f"trades: {len(pf['trades'])}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"init": cmd_init, "rebalance": cmd_rebalance,
     "settle": cmd_settle, "status": cmd_status}.get(cmd, cmd_status)()
