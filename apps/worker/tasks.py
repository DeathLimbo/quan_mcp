"""Worker task registry — one entry point per scheduler ``JobKind``.

These callables are dispatched by RQ (via :mod:`apps.worker.main`) but are
pure Python so they can be unit-tested by calling them directly with an
injected task context. The registry is the *single* place the scheduler
routes jobs; there is no dynamic `eval` or arbitrary command dispatch.

Each task takes a ``TaskContext`` that supplies the collaborators it
needs (repositories, watermarks, adapter registry). Production wires
these to Sql-backed repos; tests wire in-memory stand-ins.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from packages.common.instrument_id import InstrumentId
from packages.ingestion.pipeline import (
    IngestReport, SqlBarSink, ingest_bars_daily,
)


@dataclass
class TaskContext:
    """Bag of collaborators handed to every task callable.

    Extra keys can be added freely as more subsystems come online — using a
    dataclass rather than a dict gives typos a linter and prevents silent
    dispatch on missing dependencies.
    """
    adapter_registry: Any = None       # AdapterRegistry
    watermarks: Any = None             # WatermarkStore
    bar_repo: Any = None               # SqlBarRepository | stub
    corp_action_repo: Any = None       # SqlCorporateActionRepository | stub
    fact_repo: Any = None              # SqlFundamentalFactRepository | stub
    inference_service: Any = None      # InferenceService | stub
    evaluator: Any = None
    drift_detector: Any = None
    ledger: Any = None
    trainer: Any = None
    logger: Any = None


# --------------------------------------------------------------------
# Task callables. All take (ctx, **params) and return a JSON-safe payload.
# --------------------------------------------------------------------
def _require(ctx: TaskContext, *fields: str) -> None:
    missing = [f for f in fields if getattr(ctx, f) is None]
    if missing:
        raise RuntimeError(f"TaskContext missing required fields: {missing}")


def ingest_bars_daily_task(
    ctx: TaskContext,
    *,
    source: str,
    instrument_id: InstrumentId,
    start: date,
    end: date,
    strict: bool = True,
) -> IngestReport:
    _require(ctx, "adapter_registry", "watermarks")
    adapter = ctx.adapter_registry.get(source)
    sink = SqlBarSink(ctx.bar_repo) if ctx.bar_repo is not None else None
    return ingest_bars_daily(
        adapter, instrument_id, start, end,
        watermarks=ctx.watermarks, sink=sink, strict=strict,
    )


def ingest_corporate_actions_task(
    ctx: TaskContext, *, source: str, instrument_id: InstrumentId,
) -> dict:
    _require(ctx, "adapter_registry", "corp_action_repo")
    adapter = ctx.adapter_registry.get(source)
    actions = list(adapter.fetch_corporate_actions(instrument_id))
    written = ctx.corp_action_repo.upsert_many(actions)
    return {"instrument_id": instrument_id.canonical(),
            "source": source, "written": written}


def ingest_fundamentals_task(
    ctx: TaskContext, *, source: str, instrument_id: InstrumentId,
) -> dict:
    _require(ctx, "adapter_registry", "fact_repo")
    adapter = ctx.adapter_registry.get(source)
    facts = list(adapter.fetch_fundamentals(instrument_id))
    written = ctx.fact_repo.upsert_many(facts)
    return {"instrument_id": instrument_id.canonical(),
            "source": source, "written": written}


def run_forecast_task(
    ctx: TaskContext, *, market, horizon_days: int,
    instruments: list[InstrumentId],
) -> dict:
    _require(ctx, "inference_service")
    forecasts, no_forecasts = ctx.inference_service.run(
        market=market, horizon_days=horizon_days, instruments=instruments,
    )
    return {"forecasts": len(forecasts), "no_forecasts": len(no_forecasts)}


def evaluate_predictions_task(ctx: TaskContext, *, as_of: date) -> dict:
    _require(ctx, "evaluator")
    return {"window_end": as_of.isoformat(),
            "result": ctx.evaluator.evaluate_window(as_of - timedelta(days=7), as_of)}


def detect_drift_task(ctx: TaskContext, *, as_of: date) -> dict:
    _require(ctx, "drift_detector")
    return {"as_of": as_of.isoformat(),
            "drift": ctx.drift_detector.snapshot(as_of)}


def reconcile_ledger_task(ctx: TaskContext) -> dict:
    _require(ctx, "ledger")
    return {"ok": ctx.ledger.reconcile()}


def retrain_family_task(ctx: TaskContext, *, family: str) -> dict:
    _require(ctx, "trainer")
    return {"family": family, "run": ctx.trainer.train_family(family)}


# --------------------------------------------------------------------
# Registry — the ONLY dispatch table used by the scheduler.
# --------------------------------------------------------------------
TASK_REGISTRY: dict[str, Callable[..., Any]] = {
    "ingest_bars_daily":         ingest_bars_daily_task,
    "ingest_corporate_actions":  ingest_corporate_actions_task,
    "ingest_fundamentals":       ingest_fundamentals_task,
    "run_forecast":              run_forecast_task,
    "evaluate_predictions":      evaluate_predictions_task,
    "detect_drift":              detect_drift_task,
    "reconcile_ledger":          reconcile_ledger_task,
    "retrain_family":            retrain_family_task,
}


def dispatch(kind: str, ctx: TaskContext, **params: Any) -> Any:
    """Look up ``kind`` in the registry and invoke it. Raises on unknown."""
    if kind not in TASK_REGISTRY:
        raise KeyError(f"unknown task kind: {kind!r}")
    return TASK_REGISTRY[kind](ctx, **params)
