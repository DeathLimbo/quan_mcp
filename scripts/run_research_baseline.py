#!/usr/bin/env python3
"""Research baseline: walk-forward training + OOS evaluation — tasks #28/#29/#35.

Pulls real bars from Postgres, runs walk-forward training across a universe,
and reports OOS metrics (IC, hit-rate, score distribution). Three cross-sections:

    --market cn-fund    CN QDII funds (highly correlated — negative IC expected)
    --market cn-equity  CN A-share cross-industry leaders (dispersed — real test)
    --market us-equity  US cross-sector equities (dispersed — real test)

The OOS IC / hit-rate are honest research outputs — a negative IC is a valid
finding that *excludes* a model/universe combination (spec §34.3: "回测不是
证明，而是排除工具"), not a bug.

Usage:
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        python scripts/run_research_baseline.py --market cn-equity --trainer lightgbm
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packages.common.instrument_id import parse_instrument_id  # noqa: E402
from packages.training import walk_forward, LinearTrainer  # noqa: E402
from packages.evaluation.metrics import information_coefficient  # noqa: E402

# ---- universes -------------------------------------------------------------
# cn-fund: 7 QDII funds (all track Nasdaq — expect near-zero cross-sectional IC)
CN_FUNDS = [
    "CN.CN_FUND.FUND.019172", "CN.CN_FUND.FUND.270042", "CN.CN_FUND.FUND.160213",
    "CN.CN_FUND.FUND.017436", "CN.CN_FUND.FUND.000055", "CN.CN_FUND.FUND.007721",
    "CN.CN_FUND.FUND.018344",
]
# cn-equity: 15 A-share cross-industry leaders (food/bank/insurance/auto/
# solar/elec/elec/medical/chemical/utility/coal/appliance/appliance/liquor)
CN_A_EQUITIES = [
    "CN.SSE.EQUITY.600519", "CN.SZSE.EQUITY.300750", "CN.SSE.EQUITY.600036",
    "CN.SSE.EQUITY.601318", "CN.SZSE.EQUITY.002594", "CN.SSE.EQUITY.601012",
    "CN.SZSE.EQUITY.002415", "CN.SZSE.EQUITY.002475", "CN.SSE.EQUITY.603259",
    "CN.SSE.EQUITY.600309", "CN.SSE.EQUITY.600900", "CN.SSE.EQUITY.601088",
    "CN.SZSE.EQUITY.000333", "CN.SSE.EQUITY.600690", "CN.SZSE.EQUITY.000858",
]
# us-equity: 14 US cross-sector (tech/finance/medical/energy/retail/industrial/auto/media)
US_EQUITIES = [
    "US.NASDAQ.EQUITY.AAPL", "US.NASDAQ.EQUITY.MSFT",
    "US.NASDAQ.ETF.QQQ", "US.ARCA.ETF.SPY",
    "US.NYSE.EQUITY.JPM", "US.NYSE.EQUITY.JNJ", "US.NYSE.EQUITY.XOM",
    "US.NYSE.EQUITY.WMT", "US.NYSE.EQUITY.CAT", "US.NASDAQ.EQUITY.NVDA",
    "US.NASDAQ.EQUITY.AMZN", "US.NASDAQ.EQUITY.TSLA",
    "US.NYSE.EQUITY.BA", "US.NYSE.EQUITY.DIS",
]

FEATURES = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
            "atr_14d", "max_drawdown_20d", "price_ma_dev_20d"]


def _load_db_backends():
    spec = importlib.util.spec_from_file_location(
        "dbb", str(Path(__file__).resolve().parent.parent / "apps/quant-read-mcp/db_backends.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dbb"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward research baseline")
    ap.add_argument("--market", choices=["cn-fund", "cn-equity", "us-equity"], required=True)
    ap.add_argument("--trainer", choices=["linear", "lightgbm", "mlp"], default="lightgbm",
                    help="trainer to use (spec §13.3: prove deep > LightGBM before adopting)")
    ap.add_argument("--days", type=int, default=1825, help="history window in days")
    ap.add_argument("--horizon", type=int, default=None, help="override horizon days")
    ap.add_argument("--train-days", type=int, default=365, help="walk-forward train window")
    ap.add_argument("--db", default=os.getenv("DATABASE_URL"))
    args = ap.parse_args()
    if not args.db:
        print("ERROR: set DATABASE_URL or pass --db", file=sys.stderr)
        return 1

    if args.market == "cn-fund":
        universe = CN_FUNDS
        horizon = args.horizon or 20
    elif args.market == "cn-equity":
        universe = CN_A_EQUITIES
        horizon = args.horizon or 5
    else:
        universe = US_EQUITIES
        horizon = args.horizon or 5

    dbm = _load_db_backends()
    b = dbm.make_db_backends(args.db)
    end = date.today()
    start = end - timedelta(days=args.days)
    bars_by = {}
    for uid in universe:
        iid = parse_instrument_id(uid)
        bars = b["bar_lookup"](iid, start, end)
        if len(bars) > 60:
            bars_by[iid] = bars
    if not bars_by:
        print("ERROR: no instruments with enough bars", file=sys.stderr)
        return 2
    print(f"[{args.market}/{args.trainer}] loaded {len(bars_by)}/{len(universe)} instruments, "
          f"bars {min(len(v) for v in bars_by.values())}-{max(len(v) for v in bars_by.values())}, "
          f"horizon={horizon}d, train={args.train_days}d")

    def factory():
        if args.trainer == "lightgbm":
            from packages.training import LightGBMTrainer
            return LightGBMTrainer(FEATURES, horizon, num_boost_round=100)
        if args.trainer == "mlp":
            from packages.training import MLPTrainer
            return MLPTrainer(FEATURES, horizon, hidden=64, epochs=60)
        return LinearTrainer(FEATURES, horizon)

    wf = walk_forward(bars_by, FEATURES, horizon, trainer_factory=factory,
                      start=start, end=end, train_days=args.train_days,
                      test_days=21, step_days=21)
    preds = wf.predictions
    print(f"[{args.market}/{args.trainer}] walk-forward: {len(preds)} OOS preds, "
          f"{len(wf.model_versions)} windows")
    if not preds:
        print(f"[{args.market}/{args.trainer}] no OOS predictions (insufficient data)")
        return 0

    scores = [p.score for p in preds]
    labeled = [(p.score, p.label) for p in preds if p.label is not None]
    s_list = [s for s, _ in labeled]
    l_list = [l for _, l in labeled]
    ic = information_coefficient(s_list, l_list)
    bin_p = [1 if s > 0.5 else 0 for s in s_list]
    bin_l = [1 if l > 0 else 0 for l in l_list]
    correct = sum(1 for p, l in zip(bin_p, bin_l) if p == l)
    hit = correct / len(bin_p) if bin_p else 0.0

    print(f"[{args.market}/{args.trainer}] OOS IC={ic:.4f}")
    print(f"[{args.market}/{args.trainer}] hit_rate={hit:.2%} ({correct}/{len(bin_p)})")
    print(f"[{args.market}/{args.trainer}] score [{min(scores):.3f}, {max(scores):.3f}] "
          f"mean={sum(scores)/len(scores):.3f}")
    print(f"[{args.market}/{args.trainer}] label mean={sum(l_list)/len(l_list):.4f} "
          f"(avg {horizon}d return)")
    # verdict
    if ic > 0.10:
        print(f"[{args.market}/{args.trainer}] VERDICT: positive edge — candidate for shadow/PRODUCTION")
    elif ic > 0.05:
        print(f"[{args.market}/{args.trainer}] VERDICT: weak positive edge — candidate for shadow")
    elif ic > 0:
        print(f"[{args.market}/{args.trainer}] VERDICT: marginal — needs stronger model/features")
    else:
        print(f"[{args.market}/{args.trainer}] VERDICT: negative IC — "
              f"{args.trainer} excluded for this universe (spec §34.3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
