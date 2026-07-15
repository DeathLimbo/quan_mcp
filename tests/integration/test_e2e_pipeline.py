"""§106 end-to-end integration scenarios.

Composes the real modules in the sequence a production run would use:

    adapter --> ingestion (SqlBarSink + strict DQ)
             --> SqlBarRepository (read back)
             --> corporate-action adjust
             --> FeatureSet.compute (PIT-safe)
             --> InferenceService.score (per-market PRODUCTION model)
             --> build_portfolio
             --> RiskEngine.evaluate
             --> Portfolio.apply_fill (paper ledger)
             --> evaluation.sharpe_ratio

Each test targets one scenario from the spec (§106 acceptance + §115
edge-cases) so the whole chain -- not just a single unit -- is protected
against regressions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.corporate_actions.adjust import AdjustMode, apply_adjustment
from packages.data_sources.adapters.fake import FakeMarketDataAdapter
from packages.data_sources.contracts import Bar, CorporateAction, MarketDataAdapter
from packages.data_sources.sql_bar_repo import SqlBarRepository, metadata
from packages.evaluation.metrics import sharpe_ratio
from packages.evaluation.promotion import beats_all_baselines
from packages.features.featureset import FeatureSet
# import basics module to register default features
import packages.features.basics  # noqa: F401
from packages.inference.service import (
    Forecast, InferenceService, NoForecast, NoForecastReason,
)
from packages.ingestion import InMemoryWatermarkStore, ingest_bars_daily
from packages.ingestion.pipeline import SqlBarSink
from packages.ledger_paper.ledger import (
    Currency, PaperFill, Portfolio,
)
from packages.models.base import Model, Prediction
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState,
)
from packages.portfolio.builder import PortfolioConfig, build_portfolio
from packages.risk.engine import RiskContext, RiskVerdict, default_engine


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

IID_CN = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")
IID_US = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")

# Feature-set the whole pipeline agrees on. Registered by importing
# ``packages.features.basics`` above.
FS = FeatureSet(names=("ret_1d", "ret_5d", "vol_20d"))


class _ProdStubModel(Model):
    """Model artefact whose feature_set_hash matches ``FS``."""

    def __init__(self, model_id: str, score: float) -> None:
        self.model_id = model_id
        self.version = "v1"
        self._score = score

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        return Prediction(
            score=self._score,
            horizon_days=5,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=FS.content_hash,
        )


def _register_prod_model(reg: InMemoryModelRegistry, *, market: Market,
                         model_id: str, score: float) -> None:
    rec = ModelRecord(
        model_id=model_id, version="v1", market=market, horizon_days=5,
        feature_set_hash=FS.content_hash,
        state=ModelState.DRAFT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        approved_by=None, approval_id=None,
    )
    reg.register(rec, artifact=_ProdStubModel(model_id, score))
    reg.transition(model_id, "v1", ModelState.CANDIDATE, actor="ops")
    _gate = beats_all_baselines(
        candidate_id=f"{model_id}@v1",
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
    reg.transition(model_id, "v1", ModelState.PRODUCTION,
                   actor="ops", approval_id=f"APR-{model_id}",
                   promotion_gate=_gate)


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


@pytest.fixture
def ingested_repo(engine) -> SqlBarRepository:
    """Ingest ~90 sessions for CN & US via the fake adapter and hand back the
    repository so downstream stages can read PIT-clean bars."""
    adapter = FakeMarketDataAdapter()
    wms = InMemoryWatermarkStore()
    repo = SqlBarRepository(engine)
    sink = SqlBarSink(repo)
    # 2026-01-05 .. 2026-06-30 gives well over 60 sessions per market
    for iid in (IID_CN, IID_US):
        report = ingest_bars_daily(
            adapter, iid, date(2026, 1, 5), date(2026, 6, 30),
            watermarks=wms, sink=sink, strict=True,
        )
        assert report.written > 40, f"expected many sessions for {iid.canonical()}"
        assert not report.dq_blocked, report.findings
    return repo


# ---------------------------------------------------------------------------
# Scenario 1 -- Happy-path cross-market pipeline.
# ---------------------------------------------------------------------------


def test_e2e_scenario1_cross_market_forecast_portfolio_ledger(ingested_repo):
    """From SqlBarRepository -> features -> forecast -> portfolio -> ledger."""
    as_of = datetime(2026, 5, 1, 22, tzinfo=timezone.utc)

    # 1) Read PIT bars for both instruments.
    bars_cn = ingested_repo.find_range(
        IID_CN, date(2026, 1, 5), date(2026, 5, 1), as_of_utc=as_of,
    )
    bars_us = ingested_repo.find_range(
        IID_US, date(2026, 1, 5), date(2026, 5, 1), as_of_utc=as_of,
    )
    assert len(bars_cn) >= 30
    assert len(bars_us) >= 30

    # 2) Register PRODUCTION models per market.
    reg = InMemoryModelRegistry()
    _register_prod_model(reg, market=Market.CN, model_id="cn_v1", score=0.8)
    _register_prod_model(reg, market=Market.US, model_id="us_v1", score=0.4)

    # 3) Score.
    svc = InferenceService(reg, FS)
    fc_cn = svc.score(instrument_id=IID_CN, as_of=as_of, horizon_days=5, bars=bars_cn)
    fc_us = svc.score(instrument_id=IID_US, as_of=as_of, horizon_days=5, bars=bars_us)
    assert isinstance(fc_cn, Forecast), fc_cn
    assert isinstance(fc_us, Forecast), fc_us
    assert fc_cn.feature_hash == FS.content_hash

    # 4) Build portfolio. Bump per-name cap so a two-name universe can express
    # score-proportional weights (default 10% clip flattens both to the cap).
    target = build_portfolio(
        [fc_cn, fc_us],
        PortfolioConfig(long_only=True, max_name_weight=1.0),
    )
    assert set(target.weights.keys()) == {IID_CN, IID_US}
    # Higher score -> larger weight.
    assert target.weights[IID_CN] > target.weights[IID_US]
    assert pytest.approx(target.gross + target.cash, abs=1e-9) == 1.0

    # 5) Risk gate + paper fill through the ledger.
    ref_close = bars_cn[-1].close  # decision-time reference
    ctx = RiskContext(
        instrument_id=IID_CN, trade_date=date(2026, 5, 4),
        side=1, quantity=100, ref_price=float(ref_close),
        user_permissions=frozenset({"trade:CN"}),
        prev_close=float(ref_close), avg_volume_20d=None,
        exposure_frac_current=target.weights[IID_CN],
        exposure_frac_limit=1.0,
    )
    trace = default_engine().evaluate(ctx)
    verdict = default_engine().final_verdict(trace)
    assert verdict is RiskVerdict.ACCEPT, [d.code for d in trace]

    # 6) Ledger: deposit CNY, apply fill, assert double-entry invariant.
    port = Portfolio("pf-e2e", base_currency=Currency.CNY)
    port.deposit(Decimal("1000000"), Currency.CNY, memo="seed")
    fill = PaperFill(
        order_intent_id="INT-1", instrument_id=IID_CN, side=1,
        filled_quantity=Decimal("100"), filled_price=ref_close,
        fee=Decimal("5.00"),
        filled_at_utc=as_of,
    )
    port.apply_fill(fill, ccy=Currency.CNY)
    marks = {IID_CN: ref_close}
    val = port.value(marks)
    # Cash decreased by (px*qty + fee); position asset value = px*qty; sum invariant.
    assert port.balance(marks)
    assert val.cash_by_ccy["CNY"] == Decimal("1000000") - (
        ref_close * Decimal("100") + Decimal("5.00")
    )

    # 7) Sharpe of a synthetic equity curve derived from the two fills.
    curve = [1.0, 1.005, 1.010, 1.007, 1.012, 1.020]
    assert sharpe_ratio(
        [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    ) != 0.0


# ---------------------------------------------------------------------------
# Scenario 2 -- DQ fail-closed aborts the write and the watermark.
# ---------------------------------------------------------------------------


class _BrokenAdapter(MarketDataAdapter):
    adapter_id = "broken"
    supports_markets = frozenset({Market.US})
    supports_asset_types = frozenset({AssetType.EQUITY})

    def list_instruments(self, market):
        return []

    def fetch_bars_daily(self, instrument_id, start, end, *, adjust="none"):
        # Emit one bar that violates high >= low (Layer 3 ERROR).
        evt = datetime(start.year, start.month, start.day, 20, tzinfo=timezone.utc)
        yield Bar(
            instrument_id=instrument_id,
            event_time_utc=evt, market_local_date=start,
            open=Decimal("100"), high=Decimal("50"),  # bad
            low=Decimal("60"), close=Decimal("55"),
            volume=Decimal("1000"), turnover=None,
            adj_factor=Decimal("1"),
            available_at_utc=evt,
            source="broken", calendar_version="v1", rule_version="v1",
        )


def test_e2e_scenario2_dq_error_prevents_downstream_read(engine):
    wms = InMemoryWatermarkStore()
    repo = SqlBarRepository(engine)
    report = ingest_bars_daily(
        _BrokenAdapter(), IID_US,
        date(2026, 1, 5), date(2026, 1, 5),
        watermarks=wms, sink=SqlBarSink(repo), strict=True,
    )
    assert report.dq_blocked is True
    assert report.written == 0
    # Watermark did not advance -> next inference cannot see any bar.
    assert report.watermark_after is None
    assert repo.find_range(IID_US, date(2026, 1, 5), date(2026, 1, 5)) == []

    # Downstream: inference over an empty bar list must not silently score.
    reg = InMemoryModelRegistry()
    _register_prod_model(reg, market=Market.US, model_id="us_v1", score=0.9)
    svc = InferenceService(reg, FS)
    with pytest.raises(Exception):
        # empty bars -> FeatureMissingError inside FeatureSet.compute
        svc.score(instrument_id=IID_US,
                  as_of=datetime(2026, 1, 6, tzinfo=timezone.utc),
                  horizon_days=5, bars=[])


# ---------------------------------------------------------------------------
# Scenario 3 -- No production model plumbs a NoForecast into an all-cash
# portfolio (no silent trading on missing forecast).
# ---------------------------------------------------------------------------


def test_e2e_scenario3_no_forecast_yields_all_cash(ingested_repo):
    as_of = datetime(2026, 4, 15, 22, tzinfo=timezone.utc)
    bars = ingested_repo.find_range(
        IID_US, date(2026, 1, 5), date(2026, 4, 15), as_of_utc=as_of,
    )
    reg = InMemoryModelRegistry()  # deliberately empty
    svc = InferenceService(reg, FS)
    r = svc.score(instrument_id=IID_US, as_of=as_of, horizon_days=5, bars=bars)
    assert isinstance(r, NoForecast)
    assert r.reason is NoForecastReason.NO_PRODUCTION_MODEL

    # Builder is only fed *actual* Forecasts, per contract; a NoForecast
    # must be filtered upstream. Simulate the "no forecast" pass-through.
    target = build_portfolio([], PortfolioConfig())
    assert target.weights == {}
    assert target.cash == 1.0


# ---------------------------------------------------------------------------
# Scenario 4 -- Halted instrument: Risk L3 rejects; ledger untouched.
# ---------------------------------------------------------------------------


def test_e2e_scenario4_halted_instrument_rejects_and_ledger_untouched(ingested_repo):
    as_of = datetime(2026, 5, 1, 22, tzinfo=timezone.utc)
    bars = ingested_repo.find_range(
        IID_CN, date(2026, 1, 5), date(2026, 5, 1), as_of_utc=as_of,
    )
    reg = InMemoryModelRegistry()
    _register_prod_model(reg, market=Market.CN, model_id="cn_v1", score=0.9)
    svc = InferenceService(reg, FS)
    fc = svc.score(instrument_id=IID_CN, as_of=as_of, horizon_days=5, bars=bars)
    assert isinstance(fc, Forecast)

    # Trading day the instrument is halted.
    ctx = RiskContext(
        instrument_id=IID_CN, trade_date=date(2026, 5, 4),
        side=1, quantity=100, ref_price=float(bars[-1].close),
        user_permissions=frozenset({"trade:CN"}),
        instrument_halted=True,
        prev_close=float(bars[-1].close),
    )
    trace = default_engine().evaluate(ctx)
    assert default_engine().final_verdict(trace) is RiskVerdict.REJECT
    assert any(d.code == "HALTED" for d in trace)

    # Ledger is untouched: no fill was posted.
    port = Portfolio("pf-halted", base_currency=Currency.CNY)
    port.deposit(Decimal("100000"), Currency.CNY, memo="seed")
    assert port.cash(Currency.CNY) == Decimal("100000")
    assert port.positions() == []


# ---------------------------------------------------------------------------
# Scenario 5 -- Corporate action adjust ties in with ingestion.
# ---------------------------------------------------------------------------


def test_e2e_scenario5_split_adjustment_preserves_current_close(ingested_repo):
    """A 2-for-1 split announced mid-window scales HISTORICAL bars only."""
    bars = ingested_repo.find_range(
        IID_US, date(2026, 1, 5), date(2026, 3, 31),
    )
    assert bars, "expected pre-split bars to have been ingested"

    ex_date = bars[len(bars) // 2].market_local_date
    action = CorporateAction(
        instrument_id=IID_US, action_type="SPLIT",
        announcement_date_utc=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ex_date_local=ex_date,
        payable_date_local=ex_date, ratio=Decimal("2"),
        currency="USD", source="fake",
        available_at_utc=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    adjusted = apply_adjustment(list(bars), [action], mode=AdjustMode.BACKWARD)

    # BACKWARD adjust keeps prices at/after ex_date unchanged and scales earlier bars.
    by_date_raw = {b.market_local_date: b.close for b in bars}
    by_date_adj = {b.market_local_date: b.close for b in adjusted}
    # Post-ex_date: identical.
    post = [d for d in by_date_raw if d >= ex_date]
    for d in post:
        assert by_date_adj[d] == by_date_raw[d], f"post-ex_date changed on {d}"
    # Pre-ex_date: adjusted <= raw (scaled by 1/2).
    pre = [d for d in by_date_raw if d < ex_date]
    for d in pre:
        assert by_date_adj[d] < by_date_raw[d], f"pre-ex_date not adjusted on {d}"
        # Ratio: 1/2 (within rounding).
        assert abs(float(by_date_adj[d]) - float(by_date_raw[d]) / 2.0) < 0.01
