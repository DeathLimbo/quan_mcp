"""Scheduler declarations for periodic jobs (spec §92).

The scheduler is intentionally infrastructure-agnostic. Job definitions are
plain dataclasses; each execution target has a small renderer:

- :func:`render_airflow` -> Airflow DAG-friendly dict per job (task + schedule)
- :func:`render_n8n`     -> n8n cron-node compatible workflow spec

At runtime, jobs are enqueued onto RQ from :mod:`apps.worker` — the scheduler
never blocks on the actual work.

Each job carries the full context needed to reproduce a run:
``dataset``, ``market``, ``cron`` (in market-local time), ``timezone``,
``retries``, ``owner``, ``notes``. Adding a job is a code change subject to
review; the scheduler must not accept arbitrary shell commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


JobKind = Literal[
    "ingest_bars_daily",
    "ingest_corporate_actions",
    "ingest_fundamentals",
    "backfill_features",
    "run_forecast",
    "evaluate_predictions",
    "detect_drift",
    "reconcile_ledger",
    "retrain_family",
]


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    name: str                     # stable job id, kebab-case
    kind: JobKind
    cron: str                     # 5-field cron in ``timezone``
    timezone: str                 # IANA tz name; drives DST correctness
    queue: str = "default"
    retries: int = 2
    owner: str = "data-eng"
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


# ---- Canonical schedule (spec §92 minimum) --------------------------
_DEFAULT_JOBS: list[ScheduledJob] = [
    ScheduledJob(
        name="cn-bars-daily",
        kind="ingest_bars_daily",
        cron="30 16 * * 1-5",     # 16:30 CN local ≈ 30min after CN close
        timezone="Asia/Shanghai",
        queue="ingestion",
        owner="data-eng",
        notes="CN A-share daily close ingest.",
    ),
    ScheduledJob(
        name="us-bars-daily",
        kind="ingest_bars_daily",
        cron="30 16 * * 1-5",     # 16:30 US ET ≈ 30min after NYSE close
        timezone="America/New_York",
        queue="ingestion",
        owner="data-eng",
        notes="US equity daily close ingest.",
    ),
    ScheduledJob(
        name="fund-nav-daily",
        kind="ingest_bars_daily",
        cron="0 22 * * 1-5",      # 22:00 CN local for open-end fund NAVs
        timezone="Asia/Shanghai",
        queue="ingestion",
        owner="data-eng",
    ),
    ScheduledJob(
        name="corporate-actions-daily",
        kind="ingest_corporate_actions",
        cron="0 20 * * *",
        timezone="UTC",
        queue="ingestion",
        depends_on=("cn-bars-daily", "us-bars-daily"),
    ),
    ScheduledJob(
        name="fundamentals-daily",
        kind="ingest_fundamentals",
        cron="15 20 * * *",
        timezone="UTC",
        queue="ingestion",
    ),
    ScheduledJob(
        name="forecast-cn-daily",
        kind="run_forecast",
        cron="0 8 * * 1-5",       # pre-open CN
        timezone="Asia/Shanghai",
        queue="default",
        depends_on=("cn-bars-daily", "corporate-actions-daily"),
    ),
    ScheduledJob(
        name="forecast-us-daily",
        kind="run_forecast",
        cron="0 8 * * 1-5",       # pre-open US ET
        timezone="America/New_York",
        queue="default",
        depends_on=("us-bars-daily", "corporate-actions-daily"),
    ),
    ScheduledJob(
        name="evaluate-weekly",
        kind="evaluate_predictions",
        cron="0 3 * * SAT",       # Saturday early morning
        timezone="UTC",
        queue="evaluation",
        retries=1,
    ),
    ScheduledJob(
        name="drift-daily",
        kind="detect_drift",
        cron="30 3 * * *",
        timezone="UTC",
        queue="evaluation",
    ),
    ScheduledJob(
        name="ledger-reconcile-daily",
        kind="reconcile_ledger",
        cron="0 4 * * *",
        timezone="UTC",
        queue="default",
    ),
    ScheduledJob(
        name="retrain-cross-section-monthly",
        kind="retrain_family",
        cron="0 5 1 * *",         # 1st of month, 05:00 UTC
        timezone="UTC",
        queue="training",
        retries=0,
        notes="Cross-section families; needs approver before promotion.",
    ),
]


def schedule_definitions() -> list[ScheduledJob]:
    """Return the canonical schedule (copy so callers cannot mutate)."""
    return list(_DEFAULT_JOBS)


# ---- Renderers -----------------------------------------------------
def render_airflow(job: ScheduledJob) -> dict:
    """Airflow-friendly serialization: schedule_interval + task_id + params."""
    return {
        "dag_id": f"quant-{job.name}",
        "schedule_interval": job.cron,
        "timezone": job.timezone,
        "default_args": {
            "owner": job.owner,
            "retries": job.retries,
            "queue": job.queue,
        },
        "task_id": job.kind,
        "depends_on_past": False,
        "wait_for_downstream": bool(job.depends_on),
        "upstream": list(job.depends_on),
        "notes": job.notes,
    }


def render_n8n(job: ScheduledJob) -> dict:
    """n8n cron-node friendly workflow shell."""
    return {
        "name": f"quant-{job.name}",
        "nodes": [{
            "name": "Cron",
            "type": "n8n-nodes-base.cron",
            "parameters": {
                "cronExpression": job.cron,
                "triggerAtStartup": False,
            },
        }, {
            "name": "InvokeWorker",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST",
                "url": f"http://api/v1/internal/jobs/{job.kind}",
                "options": {"timeout": 60_000, "retry": {"maxTries": job.retries}},
            },
        }],
        "settings": {"timezone": job.timezone},
        "meta": {"owner": job.owner, "queue": job.queue,
                 "depends_on": list(job.depends_on)},
    }


def as_dict(job: ScheduledJob) -> dict:
    """Plain-dict form (used by the /v1/admin/scheduler/jobs surface)."""
    return asdict(job) | {"depends_on": list(job.depends_on)}
