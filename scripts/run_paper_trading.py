#!/usr/bin/env python3
"""Paper-trading closed loop — task #32, spec 里程碑6 Gate-4.

Full cycle: train LightGBM on real CN-fund data → deposit cash →
PaperTradingEngine.run_cycle (forecast → signal → order → fill → portfolio)
→ reconciliation + P&L. No real money moves; this is the simulated-trading
track that must pass before Gate-5 (small-capital live).

Usage:
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        python scripts/run_paper_trading.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packages.common.instrument_id import parse_instrument_id  # noqa: E402
from packages.datasets.builder import build_dataset  # noqa: E402
from packages.features import FeatureSet  # noqa: E402
from packages.ledger_paper.ledger import Currency, Portfolio  # noqa: E402
from packages.ledger_paper.simulator import SimulatedBroker  # noqa: E402
from packages.broker import SimulatedBrokerAdapter, PaperTradingEngine  # noqa: E402

CN_FUNDS = [
    "CN.CN_FUND.FUND.019172", "CN.CN_FUND.FUND.270042", "CN.CN_FUND.FUND.160213",
    "CN.CN_FUND.FUND.017436", "CN.CN_FUND.FUND.000055", "CN.CN_FUND.FUND.007721",
    "CN.CN_FUND.FUND.018344",
]
FEATURES = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
            "atr_14d", "max_drawdown_20d", "price_ma_dev_20d"]


def _load_db():
    spec = importlib.util.spec_from_file_location(
        "dbb", str(Path(__file__).resolve().parent.parent / "apps/quant-read-mcp/db_backends.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dbb"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    db = os.getenv("DATABASE_URL")
    if not db:
        print("ERROR: set DATABASE_URL", file=sys.stderr)
        return 1

    dbm = _load_db()
    b = dbm.make_db_backends(db)
    end = date.today()
    train_end = end - timedelta(days=30)
    train_start = train_end - timedelta(days=360)

    # 1) pull bars
    bars_by = {}
    for fid in CN_FUNDS:
        iid = parse_instrument_id(fid)
        bars = b["bar_lookup"](iid, train_start - timedelta(days=60), end)
        if len(bars) > 80:
            bars_by[iid] = bars
    print(f"loaded {len(bars_by)} funds")

    # 2) train LightGBM on history prior to the trading date
    train_rows = []
    for iid, bars in bars_by.items():
        train_rows.extend(build_dataset(bars, FEATURES, horizon_days=20,
                                        start=train_start, end=train_end))
    from packages.training import LightGBMTrainer
    model = LightGBMTrainer(FEATURES, 20, num_boost_round=80).fit(
        train_rows, model_id="paper_trading_lgbm")
    print(f"trained {model.model_id}@{model.version} on {len(train_rows)} rows")

    # 3) portfolio + broker
    portfolio = Portfolio("paper-001", base_currency=Currency.USD)
    portfolio.deposit(Decimal("100000"), Currency.USD)
    broker = SimulatedBrokerAdapter(
        broker=SimulatedBroker(slippage_bps=Decimal("5"), commission_bps=Decimal("3")),
        portfolio=portfolio,
    )

    # 4) forecast function
    fs = FeatureSet(tuple(FEATURES))

    def forecast_fn(iid, as_of, bars):
        try:
            feats = fs.compute(bars, as_of)
            if any(v is None for v in feats.values()):
                return None
            return model.predict_one(feats).score
        except Exception:
            return None

    # 5) run one trading cycle at the latest bar
    engine = PaperTradingEngine(
        broker=broker, portfolio=portfolio,
        buy_threshold=0.60, sell_threshold=0.40, max_weight=0.25)
    as_of = datetime.now(timezone.utc)
    result = engine.run_cycle(bars_by, as_of, forecast_fn)

    # 6) report
    print(f"\n=== TRADING CYCLE @ {as_of.isoformat()} ===")
    print(f"signals: {len(result.signals)} ({sum(1 for s in result.signals if s.side>0)} buy, "
          f"{sum(1 for s in result.signals if s.side<0)} sell)")
    for s in result.signals:
        print(f"  {s.instrument_id.canonical()} side={s.side:+d} score={s.score:.3f}")
    print(f"orders: {len(result.intents)}, fills: {len(result.fills)}")
    for f in result.fills:
        print(f"  FILL {f.instrument_id.canonical()} side={f.side:+d} "
              f"qty={f.filled_quantity} @ {f.filled_price} fee={f.fee}")
    print(f"positions: {len(result.positions)}")
    for p in result.positions:
        print(f"  {p.instrument_id.canonical()} qty={p.quantity} avg_cost={p.avg_cost}")
    print(f"cash: {dict(result.cash)}")
    # reconciliation
    filled = sum(1 for r in result.reconciliation if r.filled)
    print(f"reconciliation: {filled}/{len(result.reconciliation)} filled")
    # P&L (mark to market at latest close)
    if result.positions:
        pnl = Decimal("0")
        for p in result.positions:
            bars = bars_by.get(p.instrument_id, [])
            if bars:
                pnl += (Decimal(str(float(bars[-1].close))) - p.avg_cost) * p.quantity
        print(f"unrealized P&L: {pnl}")
    print("\npaper-trading closed loop OK (Gate-4 simulated track)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
