"""Tests for late-phase modules: ledger_paper, drift, risk.proposal, fundamentals, DQ v2."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_quality import (
    BarChecks, CANONICAL_SEVERITIES, Layer, Severity,
    business_state_check, by_severity, cross_source_bar_check,
    has_critical, has_errors,
)
from packages.data_sources.contracts import Bar
from packages.drift import (
    DriftLevel, DriftReport, ic_series, ks_stat, ood_share,
    prediction_shift_kl, psi,
)
from packages.drift.metrics import ic_trend_level, ks_level, ood_level, psi_level
from packages.fundamentals import Fact, FactName, FactStore, PitQuery, latest_as_of
from packages.ledger_paper import (
    AccountType, Currency, PaperFill, Portfolio, PortfolioValuation,
)
from packages.ledger_paper.ledger import LedgerError
from packages.risk.engine import RiskContext, RiskVerdict, default_engine
from packages.risk.proposal import ProposalStatus, RiskProposal, propose


IID_AAPL = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")
IID_MOUTAI = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")


def _bar(iid: InstrumentId, d: date, close: float, *, volume: float = 1_000_000) -> Bar:
    ts = datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc)
    return Bar(
        instrument_id=iid,
        event_time_utc=ts,
        market_local_date=d,
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        turnover=None, adj_factor=None,
        available_at_utc=ts,
        source="test", calendar_version="cal-v0", rule_version="rule-v0",
    )


# ---- ledger_paper ----------------------------------------------------------

def test_ledger_buy_then_balance_holds():
    p = Portfolio("test", base_currency=Currency.USD)
    p.deposit(Decimal("100000"), Currency.USD)
    fill = PaperFill(
        order_intent_id="i1",
        instrument_id=IID_AAPL,
        side=+1,
        filled_quantity=Decimal("10"),
        filled_price=Decimal("150.00"),
        fee=Decimal("1.00"),
        filled_at_utc=datetime(2026, 3, 2, 15, 30, tzinfo=timezone.utc),
    )
    p.apply_fill(fill, ccy=Currency.USD)
    # Cash = 100000 - (10*150 + 1) = 98499
    assert p.cash(Currency.USD) == Decimal("98499")
    marks = {IID_AAPL: Decimal("150.00")}
    val = p.value(marks)
    assert val.cash_by_ccy["USD"] == Decimal("98499")
    assert val.positions_value_by_ccy["USD"] == Decimal("1500.00")
    assert val.total_by_ccy["USD"] == Decimal("99999.00")  # -1 fee


def test_ledger_journal_entries_balanced_per_currency():
    p = Portfolio("test", base_currency=Currency.USD)
    p.deposit(Decimal("1000"), Currency.USD)
    for entry in p.journal():
        by_ccy: dict[str, Decimal] = {}
        for leg in entry.legs:
            by_ccy.setdefault(leg.account.currency.value, Decimal(0))
            by_ccy[leg.account.currency.value] += leg.delta
        for s in by_ccy.values():
            assert s == Decimal(0)


def test_ledger_rejects_oversell():
    p = Portfolio("test")
    p.deposit(Decimal("1000"), Currency.USD)
    sell = PaperFill(
        order_intent_id="s1", instrument_id=IID_AAPL, side=-1,
        filled_quantity=Decimal("1"), filled_price=Decimal("10"),
        fee=Decimal("0"), filled_at_utc=datetime(2026, 3, 2, tzinfo=timezone.utc),
    )
    with pytest.raises(LedgerError):
        p.apply_fill(sell, ccy=Currency.USD)


def test_ledger_deposit_must_be_positive():
    p = Portfolio("test")
    with pytest.raises(LedgerError):
        p.deposit(Decimal("0"), Currency.USD)


# ---- drift -----------------------------------------------------------------

def test_psi_zero_for_identical_distributions():
    xs = [float(i) for i in range(100)]
    assert psi(xs, xs) == pytest.approx(0.0, abs=1e-9)
    assert psi_level(0.0) is DriftLevel.OK


def test_psi_grows_when_distribution_shifts():
    base = [float(i) / 100 for i in range(200)]
    shifted = [x + 1.0 for x in base]  # completely disjoint
    v = psi(base, shifted)
    assert v > 0.5
    assert psi_level(v) in (DriftLevel.ALERT, DriftLevel.HALT)


def test_ks_stat_bounds():
    a = [0.0] * 100
    b = [1.0] * 100
    d = ks_stat(a, b)
    assert d == pytest.approx(1.0)
    assert ks_level(d) is DriftLevel.HALT


def test_ood_share_and_level():
    flags = [False] * 90 + [True] * 10
    s = ood_share(flags)
    assert s == pytest.approx(0.10)
    assert ood_level(s) is DriftLevel.HALT


def test_prediction_shift_kl_nonnegative():
    v = prediction_shift_kl([0.1, 0.2, 0.3, 0.4], [0.4, 0.3, 0.2, 0.1])
    assert v >= 0


def test_ic_series_and_trend():
    pairs = [([1, 2, 3], [1, 2, 3])] * 5
    ics = ic_series(pairs)
    assert all(abs(v - 1.0) < 1e-9 for v in ics)
    assert ic_trend_level(ics) is DriftLevel.OK
    bad = [-0.10] * 5
    assert ic_trend_level(bad) is DriftLevel.HALT


def test_drift_report_worst_level():
    r = DriftReport(
        feature_psi={"f1": 0.05},
        feature_ks={"f1": 0.03},
        ood_share=0.01,
        rolling_ic=[0.02, 0.03, 0.01, 0.02, 0.02],
    )
    assert r.worst_level() is DriftLevel.OK
    r2 = DriftReport(feature_psi={"f1": 0.4}, ood_share=0.0)
    assert r2.worst_level() is DriftLevel.ALERT


# ---- risk.proposal ----------------------------------------------------------

def _accept_ctx() -> RiskContext:
    return RiskContext(
        instrument_id=IID_AAPL, trade_date=date(2026, 3, 2),
        side=+1, quantity=100, ref_price=150.0,
        user_permissions=frozenset({"trade:US"}),
        prev_close=150.0, avg_volume_20d=10_000_000,
        exposure_frac_current=0.05, exposure_frac_limit=0.20,
    )


def test_proposal_approved_wraps_engine_trace():
    p = propose(_accept_ctx(), proposed_weight=0.05, policy_version="v1")
    assert p.status is ProposalStatus.APPROVED
    assert p.approved_weight == 0.05
    assert p.reasons == ()
    assert p.policy_version == "v1"
    assert p.is_tradeable()


def test_proposal_rejected_when_permission_missing():
    ctx = RiskContext(
        instrument_id=IID_AAPL, trade_date=date(2026, 3, 2),
        side=+1, quantity=100, ref_price=150.0,
        user_permissions=frozenset(),
    )
    p = propose(ctx, proposed_weight=0.05)
    assert p.status is ProposalStatus.REJECTED
    assert p.approved_weight == 0.0
    assert any("permission" in r for r in p.reasons)
    assert not p.is_tradeable()


def test_proposal_adjusted_via_bisect_on_exposure():
    """Ask for 0.4 (over limit 0.2). With build_ctx it should bisect toward 0.2."""
    def build(weight: float) -> RiskContext:
        return RiskContext(
            instrument_id=IID_AAPL, trade_date=date(2026, 3, 2),
            side=+1, quantity=100, ref_price=150.0,
            user_permissions=frozenset({"trade:US"}),
            prev_close=150.0, avg_volume_20d=10_000_000,
            exposure_frac_current=weight, exposure_frac_limit=0.20,
        )

    p = propose(build(0.40), proposed_weight=0.40, build_ctx=build)
    assert p.status is ProposalStatus.ADJUSTED
    assert 0.0 < p.approved_weight <= 0.20
    assert p.is_tradeable()


def test_proposal_safe_mode_forces_reject():
    p = propose(_accept_ctx(), proposed_weight=0.05, safe_mode=True)
    assert p.status is ProposalStatus.REJECTED
    assert p.approved_weight == 0.0
    assert p.safe_mode is True
    assert any("SAFE_MODE" in r for r in p.reasons)


# ---- fundamentals ----------------------------------------------------------

def _mk_fact(iid: InstrumentId, period: date, value: str, *,
             as_of: datetime, avail: datetime | None = None) -> Fact:
    return Fact(
        instrument_id=iid, name=FactName.EPS, period_end=period,
        value=Decimal(value), currency="USD",
        as_of_utc=as_of, available_at_utc=avail or as_of,
        source="sec", source_version="v1",
    )


def test_fundamentals_pit_returns_latest_available():
    store = FactStore()
    q1 = _mk_fact(IID_AAPL, date(2025, 3, 31), "1.50",
                  as_of=datetime(2025, 4, 30, tzinfo=timezone.utc))
    q2 = _mk_fact(IID_AAPL, date(2025, 6, 30), "1.60",
                  as_of=datetime(2025, 7, 31, tzinfo=timezone.utc))
    q3 = _mk_fact(IID_AAPL, date(2025, 9, 30), "1.70",
                  as_of=datetime(2025, 10, 31, tzinfo=timezone.utc))
    store.add_many([q3, q1, q2])
    # As of 2025-08-01 we should see q2 (avail 2025-07-31), not q3.
    result = store.get(PitQuery(IID_AAPL, FactName.EPS,
                                as_of=datetime(2025, 8, 1, tzinfo=timezone.utc)))
    assert result is not None
    assert result.value == Decimal("1.60")


def test_fundamentals_no_future_leak():
    store = FactStore()
    fact = _mk_fact(IID_AAPL, date(2025, 3, 31), "1.50",
                    as_of=datetime(2025, 4, 30, tzinfo=timezone.utc))
    store.add(fact)
    result = store.get(PitQuery(IID_AAPL, FactName.EPS,
                                as_of=datetime(2025, 4, 29, tzinfo=timezone.utc)))
    assert result is None


def test_fundamentals_rejects_broken_pit():
    with pytest.raises(Exception):
        Fact(
            instrument_id=IID_AAPL, name=FactName.EPS,
            period_end=date(2025, 3, 31), value=Decimal("1"), currency="USD",
            as_of_utc=datetime(2025, 5, 1, tzinfo=timezone.utc),
            available_at_utc=datetime(2025, 4, 30, tzinfo=timezone.utc),
            source="s", source_version="v",
        )


def test_latest_as_of_convenience():
    a = _mk_fact(IID_AAPL, date(2025, 3, 31), "1.50",
                 as_of=datetime(2025, 4, 30, tzinfo=timezone.utc))
    b = _mk_fact(IID_AAPL, date(2025, 6, 30), "1.60",
                 as_of=datetime(2025, 7, 31, tzinfo=timezone.utc))
    latest = latest_as_of([a, b], datetime(2025, 8, 1, tzinfo=timezone.utc))
    assert latest is b


# ---- data_quality v2 -------------------------------------------------------

def test_dq_layers_assigned():
    bars = [_bar(IID_AAPL, date(2026, 1, i), 100.0 + i) for i in range(1, 6)]
    findings = BarChecks().run(bars)
    # No failures on clean data
    assert not has_errors(findings)
    assert not has_critical(findings)


def test_dq_pit_violation_is_critical():
    ts_event = datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc)
    ts_avail = datetime(2026, 1, 5, 19, 0, tzinfo=timezone.utc)  # BEFORE event
    b = Bar(
        instrument_id=IID_AAPL, event_time_utc=ts_event,
        market_local_date=date(2026, 1, 5),
        open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100"),
        volume=Decimal("1000"), turnover=None, adj_factor=None,
        available_at_utc=ts_avail, source="t",
        calendar_version="v", rule_version="v",
    )
    findings = BarChecks().run([b])
    assert has_critical(findings)
    pit = [f for f in findings if f.rule == "available_after_event"][0]
    assert pit.severity is Severity.CRITICAL
    assert pit.layer is Layer.TEMPORAL


def test_dq_cross_source_disagreement():
    ba = _bar(IID_AAPL, date(2026, 1, 5), 100.00)
    bb = _bar(IID_AAPL, date(2026, 1, 5), 101.00)  # >100bps diff
    out = cross_source_bar_check([ba], [bb])
    assert out and out[0].severity is Severity.ERROR
    assert out[0].layer is Layer.CROSS_SOURCE


def test_dq_business_halted_no_volume():
    b = _bar(IID_AAPL, date(2026, 1, 5), 100.0, volume=1000)
    findings = business_state_check(b, is_halted=True)
    assert findings and findings[0].severity is Severity.CRITICAL
    assert findings[0].layer is Layer.BUSINESS


def test_dq_by_severity_folds_legacy_warn():
    bars = [_bar(IID_AAPL, date(2026, 1, i), 100.0) for i in range(1, 3)]
    findings = BarChecks().run(bars)
    buckets = by_severity(findings)
    assert set(buckets.keys()) == set(CANONICAL_SEVERITIES)
