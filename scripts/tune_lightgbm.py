#!/usr/bin/env python3
"""Quick LightGBM hyperparameter tuning via single-window holdout — task #37.

Uses one train/test split (not full walk-forward) to rapidly screen
hyperparameter combinations. The best combo is then verified via full
walk-forward in run_research_baseline.py.

Usage:
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        python scripts/tune_lightgbm.py --market cn-equity
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packages.common.instrument_id import parse_instrument_id
from packages.datasets.builder import build_dataset
from packages.training import LightGBMTrainer
from packages.evaluation.metrics import information_coefficient

CN_A_EQUITIES = [
    "CN.SSE.EQUITY.600519", "CN.SZSE.EQUITY.300750", "CN.SSE.EQUITY.600036",
    "CN.SSE.EQUITY.601318", "CN.SZSE.EQUITY.002594", "CN.SSE.EQUITY.601012",
    "CN.SZSE.EQUITY.002415", "CN.SZSE.EQUITY.002475", "CN.SSE.EQUITY.603259",
    "CN.SSE.EQUITY.600309", "CN.SSE.EQUITY.600900", "CN.SSE.EQUITY.601088",
    "CN.SZSE.EQUITY.000333", "CN.SSE.EQUITY.600690", "CN.SZSE.EQUITY.000858",
]
US_EQUITIES = [
    "US.NASDAQ.EQUITY.AAPL", "US.NASDAQ.EQUITY.MSFT",
    "US.NASDAQ.ETF.QQQ", "US.ARCA.ETF.SPY",
    "US.NYSE.EQUITY.JPM", "US.NYSE.EQUITY.JNJ", "US.NYSE.EQUITY.XOM",
    "US.NYSE.EQUITY.WMT", "US.NYSE.EQUITY.CAT", "US.NASDAQ.EQUITY.NVDA",
    "US.NASDAQ.EQUITY.AMZN", "US.NASDAQ.EQUITY.TSLA",
    "US.NYSE.EQUITY.BA", "US.NYSE.EQUITY.DIS",
]
CN_FUNDS = [
    "CN.CN_FUND.FUND.019172", "CN.CN_FUND.FUND.270042", "CN.CN_FUND.FUND.160213",
    "CN.CN_FUND.FUND.017436", "CN.CN_FUND.FUND.000055", "CN.CN_FUND.FUND.007721",
    "CN.CN_FUND.FUND.018344",
]

FEATURES = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
            "atr_14d", "max_drawdown_20d", "price_ma_dev_20d"]

# hyperparameter grid
GRID = {
    "num_boost_round": [50, 100, 200],
    "learning_rate": [0.01, 0.05, 0.1],
    "max_depth": [3, 5, -1],  # -1 = no limit (use num_leaves)
    "num_leaves": [15, 31],
}


def _load_db_backends():
    spec = importlib.util.spec_from_file_location(
        "dbb", str(Path(__file__).resolve().parent.parent / "apps/quant-read-mcp/db_backends.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dbb"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="Quick LightGBM tuning")
    ap.add_argument("--market", choices=["cn-fund", "cn-equity", "us-equity"], default="cn-equity")
    ap.add_argument("--db", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--horizon", type=int, default=None)
    args = ap.parse_args()
    if not args.db:
        print("ERROR: set DATABASE_URL", file=sys.stderr)
        return 1

    if args.market == "cn-fund":
        universe, horizon = CN_FUNDS, args.horizon or 20
    elif args.market == "cn-equity":
        universe, horizon = CN_A_EQUITIES, args.horizon or 5
    else:
        universe, horizon = US_EQUITIES, args.horizon or 5

    dbm = _load_db_backends()
    b = dbm.make_db_backends(args.db)
    end = date.today()
    start = end - timedelta(days=1825)
    train_end = end - timedelta(days=90)  # holdout: last 90d as test
    test_start = train_end

    bars_by = {}
    for uid in universe:
        iid = parse_instrument_id(uid)
        bars = b["bar_lookup"](iid, start, end)
        if len(bars) > 60:
            bars_by[iid] = bars
    print(f"[{args.market}] {len(bars_by)} instruments, horizon={horizon}d")

    train_rows, test_rows = [], []
    for iid, bars in bars_by.items():
        tr = build_dataset(list(bars), FEATURES, horizon_days=horizon, start=start, end=train_end)
        te = build_dataset(list(bars), FEATURES, horizon_days=horizon, start=test_start, end=end)
        train_rows.extend(tr)
        test_rows.extend(te)
    print(f"train: {len(train_rows)} rows, test: {len(test_rows)} rows")

    results = []
    for nbr, lr, md, nl in itertools.product(
        GRID["num_boost_round"], GRID["learning_rate"],
        GRID["max_depth"], GRID["num_leaves"],
    ):
        tr = LightGBMTrainer(FEATURES, horizon, num_boost_round=nbr,
                             learning_rate=lr, max_depth=md, num_leaves=nl)
        model = tr.fit(train_rows, model_id="tune")
        scores, labels = [], []
        for r in test_rows:
            if r.label is None:
                continue
            try:
                pred = model.predict_one(r.features)
                scores.append(pred.score)
                labels.append(r.label)
            except Exception:
                continue
        if not scores:
            continue
        ic = information_coefficient(scores, labels)
        bp = [1 if s > 0.5 else 0 for s in scores]
        bl = [1 if l > 0 else 0 for l in labels]
        hit = sum(1 for p, l in zip(bp, bl) if p == l) / len(bp)
        results.append((ic, hit, nbr, lr, md, nl))
        print(f"  nbr={nbr:3d} lr={lr:.2f} depth={md:2d} leaves={nl:2d} -> IC={ic:+.4f} hit={hit:.1%}")

    results.sort(key=lambda x: -x[0])
    print(f"\n=== TOP 3 ===")
    for ic, hit, nbr, lr, md, nl in results[:3]:
        print(f"  IC={ic:+.4f} hit={hit:.1%} | nbr={nbr} lr={lr} depth={md} leaves={nl}")
    best = results[0]
    print(f"\nBEST: IC={best[0]:+.4f} | num_boost_round={best[2]} learning_rate={best[3]} "
          f"max_depth={best[4]} num_leaves={best[5]}")
    print(f"\nVerify with: python scripts/run_research_baseline.py --market {args.market} "
          f"--trainer lightgbm --horizon {horizon}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
