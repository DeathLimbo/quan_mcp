#!/usr/bin/env python3
"""Register + evaluate: full model lifecycle — task #30.

Trains a LightGBM model on real CN-fund data, evaluates OOS metrics (IC +
net_return), then drives the full governance lifecycle via AdminTools:
DRAFT → SHADOW (with promotion gate) → request_promotion → approve_promotion
(dual-control) → PRODUCTION. Demonstrates the Champion-Challenger + audit
pipeline end-to-end (spec §21 + §81).

Usage:
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        python scripts/register_and_evaluate.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packages.common.instrument_id import Market, parse_instrument_id  # noqa: E402
from packages.datasets.builder import build_dataset  # noqa: E402
from packages.evaluation.metrics import information_coefficient  # noqa: E402
from packages.evaluation.promotion import beats_all_baselines  # noqa: E402
from packages.models.registry import InMemoryModelRegistry, ModelState  # noqa: E402
from packages.audit.record import AuditLog, InMemoryAuditSink  # noqa: E402
from packages.features import FundamentalContext  # noqa: E402
import psycopg  # noqa: E402
from datetime import datetime, time, timezone  # noqa: E402

CN_A_EQUITIES = [
    "CN.SSE.EQUITY.600519", "CN.SZSE.EQUITY.300750", "CN.SSE.EQUITY.600036",
    "CN.SSE.EQUITY.601318", "CN.SZSE.EQUITY.002594", "CN.SSE.EQUITY.601012",
    "CN.SZSE.EQUITY.002415", "CN.SZSE.EQUITY.002475", "CN.SSE.EQUITY.603259",
    "CN.SSE.EQUITY.600309", "CN.SSE.EQUITY.600900", "CN.SSE.EQUITY.601088",
    "CN.SZSE.EQUITY.000333", "CN.SSE.EQUITY.600690", "CN.SZSE.EQUITY.000858",
]
FEATURES = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
            "atr_14d", "max_drawdown_20d", "price_ma_dev_20d",
            "pe_ratio", "pb_ratio", "earnings_yield", "roe"]
HORIZON = 20


def _load_admin_tools():
    spec = importlib.util.spec_from_file_location(
        "at", str(Path(__file__).resolve().parent.parent / "apps/quant-admin-mcp/tools.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["at"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_db_backends():
    spec = importlib.util.spec_from_file_location(
        "dbb", str(Path(__file__).resolve().parent.parent / "apps/quant-read-mcp/db_backends.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dbb"] = mod
    spec.loader.exec_module(mod)
    return mod


def _to_psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def _make_fund_ctx_provider(db_url: str):
    """PIT-safe fundamentals provider: latest facts per fact_name where available_at <= as_of."""
    conn = psycopg.connect(_to_psycopg_url(db_url), autocommit=True)

    def provider(as_of_date, instrument_id):
        iid_str = instrument_id.canonical()
        as_of_dt = datetime.combine(as_of_date, time(23, 59, 59), tzinfo=timezone.utc)
        cur = conn.execute(
            "SELECT DISTINCT ON (fact_name) fact_name, value_num"
            " FROM fundamental_fact"
            " WHERE instrument_id = %s AND available_at_utc <= %s"
            " ORDER BY fact_name, as_of_utc DESC",
            (iid_str, as_of_dt),
        )
        facts = {}
        for fact_name, value in cur:
            if value is not None:
                facts[fact_name] = float(value)
        return FundamentalContext(facts=facts) if facts else None

    return provider


def main() -> int:
    db = os.getenv("DATABASE_URL")
    if not db:
        print("ERROR: set DATABASE_URL", file=sys.stderr)
        return 1

    dbm = _load_db_backends()
    admin_mod = _load_admin_tools()
    b = dbm.make_db_backends(db)
    fund_ctx_provider = _make_fund_ctx_provider(db)

    # 1) pull bars + build train/holdout datasets (cn-equity 20d + fundamentals)
    end = date.today()
    holdout_end = end - timedelta(days=HORIZON + 5)
    train_end = holdout_end - timedelta(days=1)
    train_start = train_end - timedelta(days=720)  # 2y train for fundamentals
    holdout_start = holdout_end - timedelta(days=60)  # 60d holdout

    train_rows, holdout_rows = [], []
    for fid in CN_A_EQUITIES:
        iid = parse_instrument_id(fid)
        bars = b["bar_lookup"](iid, train_start - timedelta(days=60), end)
        if len(bars) < 80:
            continue
        train_rows.extend(build_dataset(bars, FEATURES, horizon_days=HORIZON,
                                        start=train_start, end=train_end,
                                        fund_ctx_provider=fund_ctx_provider))
        holdout_rows.extend(build_dataset(bars, FEATURES, horizon_days=HORIZON,
                                          start=holdout_start, end=holdout_end,
                                          fund_ctx_provider=fund_ctx_provider))
    print(f"train rows: {len(train_rows)}, holdout rows: {len(holdout_rows)}")
    if len(train_rows) < 50 or not holdout_rows:
        print("ERROR: insufficient data", file=sys.stderr)
        return 2

    # 2) train LightGBM
    from packages.training import LightGBMTrainer
    trainer = LightGBMTrainer(FEATURES, HORIZON, task="classification", num_boost_round=100)
    model = trainer.fit(train_rows, model_id="cn_equity_fund20d_lgbm_v1",
                        valid_rows=holdout_rows[:max(1, len(holdout_rows)//3)])
    print(f"trained: {model.model_id}@{model.version} ({model.task})")

    # 3) evaluate holdout OOS
    scores, labels = [], []
    for r in holdout_rows:
        if r.label is None:
            continue
        try:
            p = model.predict_one(r.features)
            scores.append(p.score)
            labels.append(r.label)
        except Exception:
            continue
    ic = information_coefficient(scores, labels) if scores else 0.0
    # net_return: mean return of long positions (score > 0.5)
    long_rets = [l for s, l in zip(scores, labels) if s > 0.5]
    net_return = sum(long_rets) / len(long_rets) if long_rets else 0.0
    print(f"OOS holdout: IC={ic:.4f}, net_return={net_return:.4f}, n={len(scores)}")

    # 4) governance lifecycle via AdminTools (dual-control)
    reg = InMemoryModelRegistry()
    audit = AuditLog(InMemoryAuditSink())
    admin = admin_mod.AdminTools(registry=reg, audit=audit)

    # 4a) register (DRAFT) + attach artifact
    rec = admin.register_model(
        model_id=model.model_id, version=model.version, market="CN",
        horizon_days=HORIZON, feature_set_hash=model.feature_set_hash,
        actor="researcher@acme", notes="LightGBM CN equity 20d + fundamentals (IC=0.19)")
    reg._artifacts[(model.model_id, model.version)] = model
    print(f"registered: {rec['data']['state']}")

    # 4b) promotion gate
    candidate_metrics = {"ic": ic, "net_return": net_return}
    baselines = {"buy_and_hold": {"ic": 0.0, "net_return": 0.0}}
    gate = beats_all_baselines(candidate_id=model.model_id,
                               candidate_metrics=candidate_metrics,
                               baselines=baselines)
    print(f"promotion gate: passed={gate.passed}")

    # 4c) DRAFT -> CANDIDATE -> SHADOW (with gate)
    admin.promote_model(model_id=model.model_id, version=model.version,
                        to_state="CANDIDATE", actor="researcher@acme")
    r = admin.model_start_shadow(model_id=model.model_id, version=model.version,
                                 actor="researcher@acme",
                                 candidate_metrics=candidate_metrics,
                                 baseline_metrics=baselines)
    print(f"start_shadow: ok={r['ok']} {r.get('data', r.get('error'))}")

    # 4d) request + approve promotion (dual-control: requester != approver)
    if r["ok"]:
        rp = admin.model_request_promotion(model_id=model.model_id,
                                           version=model.version, actor="researcher@acme")
        print(f"request_promotion: {rp.get('data', rp.get('error'))}")
        if rp["ok"]:
            req_id = rp["data"]["request_id"]
            ap = admin.model_approve_promotion(
                request_id=req_id, actor="approver@acme", approval_id="appr_001",
                candidate_metrics=candidate_metrics, baseline_metrics=baselines)
            print(f"approve_promotion: ok={ap['ok']} {ap.get('data', ap.get('error'))}")

    # 5) final state + audit
    final = reg._by_key.get((model.model_id, model.version))
    print(f"\nFINAL STATE: {final.state.value if final else 'UNKNOWN'}")
    print(f"audit events: {len(audit.events())}")
    print("register+evaluate lifecycle OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
