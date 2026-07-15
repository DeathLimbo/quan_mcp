"""Worker task registry tests — verify scheduler.JobKind coverage + dispatch."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from apps.scheduler import schedule_definitions
from apps.worker.tasks import TASK_REGISTRY, TaskContext, dispatch
from packages.common.instrument_id import parse_instrument_id
from packages.data_sources.contracts import Bar
from packages.data_sources.registry import AdapterRegistry
from packages.ingestion.watermark import InMemoryWatermarkStore


def test_every_job_kind_has_a_task():
    """The scheduler must not carry a JobKind without a matching dispatch entry."""
    missing = []
    for job in schedule_definitions():
        if job.kind not in TASK_REGISTRY:
            missing.append(job.kind)
    assert not missing, f"scheduler kinds missing dispatch: {missing}"


def test_dispatch_unknown_kind_raises():
    with pytest.raises(KeyError):
        dispatch("does_not_exist", TaskContext())


def test_ingest_bars_daily_dispatch_end_to_end():
    iid = parse_instrument_id("US.NASDAQ.EQUITY.AAPL")

    class _Stub:
        adapter_id = "stub"

        def fetch_bars_daily(self, iid, start, end):
            return iter([Bar(
                instrument_id=iid,
                event_time_utc=datetime(2024, 6, 5, 20, tzinfo=timezone.utc),
                market_local_date=date(2024, 6, 5),
                open=Decimal("100"), high=Decimal("100"),
                low=Decimal("100"), close=Decimal("100"),
                volume=Decimal("1000"), turnover=None,
                adj_factor=Decimal("1"),
                available_at_utc=datetime(2024, 6, 5, 21, tzinfo=timezone.utc),
                source="stub", calendar_version="v0", rule_version="v0",
            )])

    class _Sink:
        def __init__(self):
            self.rows: list = []

        def upsert_many(self, bars):
            self.rows.extend(bars)
            return len(self.rows)

    ctx = TaskContext(
        adapter_registry=AdapterRegistry({"stub": _Stub()}),
        watermarks=InMemoryWatermarkStore(),
        bar_repo=_Sink(),
    )
    report = dispatch("ingest_bars_daily", ctx,
                      source="stub", instrument_id=iid,
                      start=date(2024, 6, 1), end=date(2024, 6, 10))
    assert report.written == 1
    assert report.dq_blocked is False


def test_missing_collaborators_are_reported_clearly():
    with pytest.raises(RuntimeError) as excinfo:
        dispatch("run_forecast", TaskContext(),
                 market=None, horizon_days=5, instruments=[])
    assert "inference_service" in str(excinfo.value)


def test_reconcile_ledger_uses_injected_ledger():
    class _Ledger:
        def reconcile(self):
            return True
    ctx = TaskContext(ledger=_Ledger())
    result = dispatch("reconcile_ledger", ctx)
    assert result == {"ok": True}


def test_evaluate_predictions_forwards_window():
    class _Eval:
        def evaluate_window(self, start, end):
            return {"start": start.isoformat(), "end": end.isoformat()}
    ctx = TaskContext(evaluator=_Eval())
    result = dispatch("evaluate_predictions", ctx, as_of=date(2024, 6, 10))
    assert result["result"]["end"] == "2024-06-10"


def test_retrain_family_uses_injected_trainer():
    class _T:
        def train_family(self, family):
            return {"family": family, "status": "queued"}
    ctx = TaskContext(trainer=_T())
    result = dispatch("retrain_family", ctx, family="CN_ETF_SHORT_C")
    assert result["family"] == "CN_ETF_SHORT_C"
