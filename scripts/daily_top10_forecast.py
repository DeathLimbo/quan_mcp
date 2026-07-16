#!/usr/bin/env python3
"""Daily Top-10 forecast — pre-open return prediction for CN + US + funds.

Trains/regresses three LightGBM models (CN fundamentals, US price-volume, fund
NAV price-volume), predicts the latest available day for every instrument, and
emits an HTML report of the top 10 by expected 20-day return.

Model persistence: PRODUCTION models are stored in the model_registry table +
on-disk artifacts (migration 0011). On each run the script first tries to load
the cached PRODUCTION model (milliseconds); only if absent (or --retrain) does
it train, persist, and promote. This lets models accumulate learning across
runs instead of being retrained from scratch every day.

Usage:
    DATABASE_URL=postgresql+psycopg://quant:quant@localhost:5432/quant \
        python scripts/daily_top10_forecast.py [--retrain] [--market all]

Triggered daily pre-open:
    A-shares 09:00 Beijing (UTC 01:00) — uses prior close bars
    US        21:30 Beijing (UTC 13:30) — uses prior US close bars
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
import sqlalchemy as sa
from packages.common.instrument_id import Market, parse_instrument_id
from packages.common.time_utils import utcnow
from packages.datasets.builder import build_dataset
from packages.features import FundamentalContext
from packages.models.registry import ModelRecord, ModelState
from packages.persistence.repositories import SqlModelRegistry
from packages.training import LightGBMTrainer

# ---- universes -----------------------------------------------------------
CN_A_EQUITIES = [
    "CN.SSE.EQUITY.600519", "CN.SZSE.EQUITY.300750", "CN.SSE.EQUITY.600036",
    "CN.SSE.EQUITY.601318", "CN.SZSE.EQUITY.002594", "CN.SSE.EQUITY.601012",
    "CN.SZSE.EQUITY.002415", "CN.SZSE.EQUITY.002475", "CN.SSE.EQUITY.603259",
    "CN.SSE.EQUITY.600309", "CN.SSE.EQUITY.600900", "CN.SSE.EQUITY.601088",
    "CN.SZSE.EQUITY.000333", "CN.SSE.EQUITY.600690", "CN.SZSE.EQUITY.000858",
]
US_EQUITIES = [
    "US.NASDAQ.EQUITY.AAPL", "US.NASDAQ.EQUITY.MSFT", "US.NASDAQ.EQUITY.NVDA",
    "US.NASDAQ.EQUITY.AMZN", "US.NASDAQ.EQUITY.TSLA", "US.NYSE.EQUITY.JPM",
    "US.NYSE.EQUITY.JNJ", "US.NYSE.EQUITY.CAT", "US.NYSE.EQUITY.DIS",
    "US.NYSE.EQUITY.BA", "US.NYSE.EQUITY.XOM", "US.NYSE.EQUITY.WMT",
    "US.NASDAQ.ETF.QQQ", "US.ARCA.ETF.SPY",
]
FUNDS = [
    "CN.CN_FUND.FUND.019172", "CN.CN_FUND.FUND.270042", "CN.CN_FUND.FUND.160213",
    "CN.CN_FUND.FUND.017436", "CN.CN_FUND.FUND.000055", "CN.CN_FUND.FUND.025208",
    "CN.CN_FUND.FUND.007721", "CN.CN_FUND.FUND.018344",
]
FUND_NAMES = {
    "CN.CN_FUND.FUND.019172": "摩根纳斯达克100(QDII)A",
    "CN.CN_FUND.FUND.270042": "广发纳指100ETF联接(QDII)A",
    "CN.CN_FUND.FUND.160213": "国泰纳斯达克100(QDII)",
    "CN.CN_FUND.FUND.017436": "华宝纳斯达克精选(QDII)A",
    "CN.CN_FUND.FUND.000055": "广发纳指100ETF联接(QDII)美元A",
    "CN.CN_FUND.FUND.025208": "永赢先锋半导体智选A",
    "CN.CN_FUND.FUND.007721": "天弘标普500(QDII-FOF)A",
    "CN.CN_FUND.FUND.018344": "华夏中证机器人ETF联接A",
}
CN_FEATURES = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
               "atr_14d", "max_drawdown_20d", "price_ma_dev_20d",
               "pe_ratio", "pb_ratio", "earnings_yield", "roe"]
PV_FEATURES = ["ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
               "atr_14d", "max_drawdown_20d", "price_ma_dev_20d"]
HORIZON = 20
MODEL_STORE = "/Volumes/Elements/quan_mcp/model_store"


def _to_psycopg_url(url):
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def _load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name, str(Path(__file__).resolve().parent.parent / path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_fund_ctx_provider(db_url):
    conn = psycopg.connect(_to_psycopg_url(db_url), autocommit=True)
    def provider(as_of_date, instrument_id):
        as_of_dt = datetime.combine(as_of_date, datetime.max.time(), tzinfo=timezone.utc)
        cur = conn.execute(
            "SELECT DISTINCT ON (fact_name) fact_name, value_num"
            " FROM fundamental_fact"
            " WHERE instrument_id = %s AND available_at_utc <= %s"
            " ORDER BY fact_name, as_of_utc DESC",
            (instrument_id.canonical(), as_of_dt))
        facts = {fn: float(v) for fn, v in cur if v is not None}
        return FundamentalContext(facts=facts) if facts else None
    return provider, conn


def _train_raw(b, universe, features, model_id, fund_ctx=None):
    """Train a regression LightGBM on the trailing ~2y window. Returns (model, n_rows)."""
    end = date.today()
    train_start = end - timedelta(days=720)
    train_end = end - timedelta(days=HORIZON + 5)
    rows = []
    for fid in universe:
        iid = parse_instrument_id(fid)
        bars = b["bar_lookup"](iid, train_start - timedelta(days=60), end)
        if len(bars) > 80:
            rows.extend(build_dataset(bars, features, horizon_days=HORIZON,
                                      start=train_start, end=train_end,
                                      fund_ctx_provider=fund_ctx))
    if not rows:
        return None, 0
    trainer = LightGBMTrainer(features, HORIZON, task="regression",
                              num_boost_round=100)
    model = trainer.fit(rows, model_id=model_id)
    return model, len(rows)


def _get_or_train_model(sql_reg, b, model_id, market, universe, features,
                        fund_ctx=None, force_retrain=False):
    """Load cached PRODUCTION model from DB; train+persist only if absent or --retrain."""
    if not force_retrain:
        rec, artifact = sql_reg.get_latest_production(model_id)
        if artifact is not None:
            age = (utcnow() - rec.created_at).days
            print(f"    loaded PRODUCTION {model_id}@{rec.version[:8]} from DB "
                  f"(cached {age}d ago, {rec.metrics.get('train_rows', '?')} rows)")
            return artifact
    model, n = _train_raw(b, universe, features, model_id, fund_ctx)
    if model is None:
        return None
    rec = ModelRecord(
        model_id=model_id, version=model.version, market=market,
        horizon_days=HORIZON, feature_set_hash=model.feature_set_hash,
        state=ModelState.DRAFT, created_at=utcnow(),
        approved_by=None, approval_id=None, metrics={}, notes="auto daily train")
    sql_reg.register(rec, artifact=model, metrics={"train_rows": float(n)})
    sql_reg.transition(model_id, model.version, ModelState.PRODUCTION,
                       actor="daily_forecast@auto", approval_id="auto_daily",
                       metrics={"train_rows": float(n)})
    print(f"    trained {n} rows → PRODUCTION {model_id}@{model.version[:8]} (persisted)")
    return model


def _latest_forecast(b, model, universe, features, fund_ctx=None):
    """Predict the latest available day for every instrument; return list of dicts
    with expected_return (raw 20d return) + confidence (direction probability)."""
    end = date.today()
    start = end - timedelta(days=400)
    out = []
    for fid in universe:
        iid = parse_instrument_id(fid)
        bars = b["bar_lookup"](iid, start, end)
        if len(bars) < 80:
            continue
        rows = build_dataset(bars, features, horizon_days=HORIZON,
                             start=end - timedelta(days=10), end=end,
                             fund_ctx_provider=fund_ctx)
        if not rows:
            continue
        latest = max(rows, key=lambda r: r.as_of_date)
        try:
            ret = model.predict_return(latest.features)
            conf = float(model.predict_one(latest.features).score)
        except Exception:
            continue
        if ret is None:  # not a regression model — skip
            continue
        last_bar = bars[-1]
        out.append({
            "iid": iid.canonical(),
            "short": iid.symbol,
            "name": FUND_NAMES.get(iid.canonical(), iid.symbol),
            "market": iid.market.value,
            "expected_return": float(ret),
            "confidence": conf,
            "as_of": latest.as_of_date.isoformat(),
            "last_close": float(last_bar.close),
        })
    return out


def _render_html(top10, meta, out_path):
    when = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 北京")
    rows_html = []
    for i, f in enumerate(top10, 1):
        ret = f["expected_return"]
        ret_pct = f"{ret*100:+.2f}%"
        conf = f["confidence"]
        # Chinese convention: red = bullish (positive return), green = bearish
        color = "#d32f2f" if ret > 0 else ("#2e7d32" if ret < 0 else "#757575")
        direction = "看涨" if ret > 0 else ("看跌" if ret < 0 else "中性")
        conf_bar = f"{conf*100:.0f}%"
        rows_html.append(
            f"<tr><td>{i}</td><td><b>{html.escape(f['short'])}</b><br>"
            f"<span class='sub'>{html.escape(f['name'])}</span></td>"
            f"<td>{f['market']}</td>"
            f"<td class='ret' style='color:{color}'>{ret_pct}</td>"
            f"<td style='color:{color}'>{direction}</td>"
            f"<td>{conf_bar}</td>"
            f"<td>{f['as_of']}</td>"
            f"<td>{f['last_close']:.4f}</td></tr>")
    models_html = "".join(
        f"<li><b>{m['id']}</b> — {m['mode']}, "
        f"train {m.get('rows','?')} rows, {m.get('age','new')}</li>"
        for m in meta["models"])
    doc = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日 Top-10 收益预测 — {when[:10]}</title>
<style>
body{{font-family:-apple-system,'PingFang SC',sans-serif;margin:0;background:#fafafa;color:#212121}}
.wrap{{max-width:920px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}}
.sub{{color:#757575;font-size:12px}}
.meta{{color:#616161;font-size:13px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08);border-radius:8px;overflow:hidden}}
th{{background:#f5f5f5;text-align:left;padding:10px 12px;font-size:13px;color:#424242}}
td{{padding:10px 12px;border-top:1px solid #eee;font-size:14px;vertical-align:top}}
.ret{{font-weight:700;font-size:15px}}
.foot{{margin-top:18px;font-size:12px;color:#9e9e9e;line-height:1.6}}
ul{{margin:6px 0;padding-left:20px}}
.disclaimer{{margin-top:14px;padding:10px 12px;background:#fff3e0;border-left:3px solid #ff9800;font-size:12px;color:#e65100;border-radius:4px}}
</style></head><body><div class="wrap">
<h1>🦫 每日 Top-10 收益预测</h1>
<div class="meta">生成于 {when} · horizon {HORIZON}d · 回归模型预测预期收益率 · 三模型融合排序</div>
<table>
<tr><th>#</th><th>标的</th><th>市场</th><th>预期收益率</th><th>方向</th><th>置信度</th><th>数据日</th><th>最新收盘</th></tr>
{"".join(rows_html)}
</table>
<div class="foot">
<b>模型：</b><ul>{models_html}</ul>
<b>预测窗口：</b>未来 {HORIZON} 个交易日预期收益率（回归模型直接输出幅度，非仅方向）。
<b>排序：</b>CN A股 + 美股 + 持仓基金 三 universe 预测合并，按预期收益率降序取前 10。
<b>置信度：</b>方向概率（涨的概率），辅助判断信号强度。
<b>持久化：</b>模型存 model_registry 表 + 磁盘 artifact，跨运行累积；每日加载缓存模型（秒级），仅无缓存或 --retrain 时重训。
</div>
<div class="disclaimer">⚠️ 量化预测仅供研究参考，不构成投资建议。模型基于历史数据，可能因市场结构变化失效。
预期收益率为模型估计，非收益保证。请结合风控与基本面独立决策。</div>
</div></body></html>"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(doc, encoding="utf-8")


def main() -> int:
    db = os.getenv("DATABASE_URL")
    if not db:
        print("ERROR: set DATABASE_URL", file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--market", default="all", choices=["all", "cn", "us", "fund"])
    ap.add_argument("--retrain", action="store_true",
                    help="force retrain even if a cached PRODUCTION model exists")
    args = ap.parse_args()

    dbm = _load_module("dbb", "apps/quant-read-mcp/db_backends.py")
    b = dbm.make_db_backends(db)
    fund_ctx, fund_conn = _make_fund_ctx_provider(db)
    engine = sa.create_engine(db)
    sql_reg = SqlModelRegistry(engine, MODEL_STORE)

    end = date.today()
    print(f"Daily Top-10 return forecast — as_of {end} (market={args.market}, retrain={args.retrain})")

    all_fc = []
    models_meta = []

    if args.market in ("all", "cn"):
        print("  CN A-shares:")
        m = _get_or_train_model(sql_reg, b, "cn_equity_reg20d", Market.CN,
                                CN_A_EQUITIES, CN_FEATURES, fund_ctx, args.retrain)
        if m:
            fc = _latest_forecast(b, m, CN_A_EQUITIES, CN_FEATURES, fund_ctx)
            for f in fc: f["model"] = "cn_equity_reg20d"
            all_fc.extend(fc)
            models_meta.append({"id": "cn_equity_reg20d", "mode": "regression",
                                "rows": m.feature_set_hash[:8], "age": "loaded" if not args.retrain else "retrained"})

    if args.market in ("all", "us"):
        print("  US equities:")
        m = _get_or_train_model(sql_reg, b, "us_equity_reg20d", Market.US,
                                US_EQUITIES, PV_FEATURES, None, args.retrain)
        if m:
            fc = _latest_forecast(b, m, US_EQUITIES, PV_FEATURES)
            for f in fc: f["model"] = "us_equity_reg20d"
            all_fc.extend(fc)
            models_meta.append({"id": "us_equity_reg20d", "mode": "regression",
                                "rows": m.feature_set_hash[:8], "age": "loaded" if not args.retrain else "retrained"})

    if args.market in ("all", "fund"):
        print("  Funds:")
        m = _get_or_train_model(sql_reg, b, "fund_nav_reg20d", Market.CN,
                                FUNDS, PV_FEATURES, None, args.retrain)
        if m:
            fc = _latest_forecast(b, m, FUNDS, PV_FEATURES)
            for f in fc: f["model"] = "fund_nav_reg20d"
            all_fc.extend(fc)
            models_meta.append({"id": "fund_nav_reg20d", "mode": "regression",
                                "rows": m.feature_set_hash[:8], "age": "loaded" if not args.retrain else "retrained"})

    fund_conn.close()

    if not all_fc:
        print("ERROR: no forecasts produced", file=sys.stderr)
        return 2

    all_fc.sort(key=lambda x: x["expected_return"], reverse=True)
    top10 = all_fc[:10]

    print("\n" + "=" * 64)
    print(f"TOP 10 by expected {HORIZON}d return (of {len(all_fc)} forecasts)")
    print("=" * 64)
    for i, f in enumerate(top10, 1):
        print(f"  {i:2d}. {f['short']:8s} {f['name'][:22]:22s} "
              f"{f['expected_return']*100:+6.2f}%  conf {f['confidence']*100:4.0f}%  "
              f"[{f['market']}]  as_of={f['as_of']}")

    out = args.out or str(
        Path("/Users/xxx/Workbuddy/Claw/华尔街之狼/reports") /
        f"{end.isoformat()}_top10_forecast.html")
    out = os.path.normpath(out)
    _render_html(top10, {"models": models_meta}, out)
    print(f"\nHTML: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
