"""Extended Golden Dataset scenarios (spec §115).

Complements ``test_golden_scenarios.py`` with:
- CN halt-lift + trading resumption
- US cross-source close-price disagreement DQ layer 4
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from packages.backtest.execution import (
    CnExecutionModel, Fill, OrderIntent, Side,
)
from packages.common.instrument_id import parse_instrument_id
from packages.data_sources.contracts import Bar
from tests.fixtures import load_scenario


def _fx_bar(row: dict) -> Bar:
    """Convert a JSONL bar row into a Bar (halted bars carry no prices)."""
    iid = parse_instrument_id(row["instrument_id"])
    d = date.fromisoformat(row["date"])
    et = datetime(d.year, d.month, d.day, 20, tzinfo=timezone.utc)
    if row.get("halted"):
        zero = Decimal("0")
        return Bar(instrument_id=iid, event_time_utc=et, market_local_date=d,
                   open=zero, high=zero, low=zero, close=zero, volume=zero,
                   turnover=None, adj_factor=Decimal("1"),
                   available_at_utc=et, source="golden",
                   calendar_version="v0", rule_version="v0")
    return Bar(
        instrument_id=iid, event_time_utc=et, market_local_date=d,
        open=Decimal(row["o"]), high=Decimal(row["h"]),
        low=Decimal(row["l"]), close=Decimal(row["c"]),
        volume=Decimal(row["v"]), turnover=None,
        adj_factor=Decimal("1"), available_at_utc=et,
        source="golden", calendar_version="v0", rule_version="v0",
    )


# --------------------------------------------------------------------
# Halt-lift resumption (CN)
# --------------------------------------------------------------------
def test_cn_halt_lift_fills_on_first_open_day():
    """Order submitted on 01-03; 01-04 and 01-05 halted; 01-08 trading resumes.
    Executor should skip halted days and fill on the first non-halted bar."""
    rows = load_scenario("cn", "halt_lift_resume")
    # In the real pipeline, halted days are filtered from the "tradable" bar
    # set (volume=0 sentinels never reach the executor). We reproduce that
    # here so the executor's next-bar search naturally skips 01-04/01-05
    # and lands on 01-08.
    bars = [_fx_bar(r) for r in rows
            if r["type"] == "bar" and not r.get("halted")]
    halted = frozenset(date.fromisoformat(r["date"])
                       for r in rows if r["type"] == "bar" and r.get("halted"))
    order_row = next(r for r in rows if r["type"] == "order")
    intent = OrderIntent(
        instrument_id=parse_instrument_id(order_row["instrument_id"]),
        side=Side(order_row["side"]),
        quantity=Decimal(str(order_row["qty"])),
        decision_date=date.fromisoformat(order_row["submit_date"]),
    )
    result = CnExecutionModel().execute(intent, bars=bars, halted_dates=halted)
    assert isinstance(result, Fill)
    # First tradable day after decision (01-03) and after halts is 01-08.
    assert result.fill_date == date(2024, 1, 8)


# --------------------------------------------------------------------
# Cross-source disagreement (US)
# --------------------------------------------------------------------
def test_us_cross_source_disagreement_flags_error():
    """20bps threshold flags ~300bps discrepancy on 2024-06-05."""
    from packages.data_quality.checks import Severity, cross_source_bar_check
    rows = load_scenario("us", "cross_source_disagreement")
    iid = parse_instrument_id("US.NASDAQ.EQUITY.AAPL")

    def _mk(c: str, d: str) -> Bar:
        dd = date.fromisoformat(d)
        et = datetime(dd.year, dd.month, dd.day, 20, tzinfo=timezone.utc)
        return Bar(
            instrument_id=iid, event_time_utc=et, market_local_date=dd,
            open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c),
            volume=Decimal("1000"), turnover=None, adj_factor=Decimal("1"),
            available_at_utc=et, source="golden",
            calendar_version="v0", rule_version="v0",
        )
    primary = [_mk(r["c"], r["date"]) for r in rows if r["type"] == "primary"]
    secondary = [_mk(r["c"], r["date"]) for r in rows if r["type"] == "secondary"]
    findings = cross_source_bar_check(primary, secondary, bps_tolerance=20)
    # First two dates within tolerance; only 06-05 should trigger.
    assert len(findings) == 1
    assert findings[0].reference == "2024-06-05"
    assert findings[0].severity is Severity.ERROR  # >100bps => ERROR
    assert findings[0].rule == "close_agreement"
