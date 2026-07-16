"""Scheduler execution engine — spec §17 多市场自动调度 + §92.

The :mod:`apps.scheduler.schedule` module declares *what* runs and *when*
(declarative ``ScheduledJob`` dataclasses rendered to Airflow/n8n). This
module adds the *execution* layer: a ``SchedulerExecutor`` that decides which
jobs are due (via croniter, DST-correct), dispatches them through
:func:`apps.worker.tasks.dispatch`, retries on failure, and keeps a run
history.

Two backends:
- **in-process** (default): ``run_due()`` synchronously calls ``dispatch``.
  Fine for dev / single-node.
- **RQ** (``redis_url`` set): enqueues onto an RQ queue; a separate
  ``apps.worker`` process consumes. Production path (spec §17.5).

Runtime parameters (instrument_id / date_range / market) are injected via a
``params_provider`` callback so the executor stays business-logic-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from apps.scheduler.schedule import ScheduledJob, schedule_definitions


@dataclass
class JobRunRecord:
    job_name: str
    kind: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "RUNNING"          # RUNNING / SUCCEEDED / FAILED
    attempts: int = 0
    error: str | None = None
    result: Any | None = None


@dataclass
class SchedulerExecutor:
    """Cron-driven execution engine over :class:`ScheduledJob` definitions.

    ``ctx`` is a :class:`apps.worker.tasks.TaskContext` carrying the wired
    backends (adapter_registry / inference_service / evaluator / etc.).
    ``params_provider(job) -> dict`` supplies the runtime kwargs that
    :func:`apps.worker.tasks.dispatch` expects for each job kind.
    """

    jobs: Sequence[ScheduledJob] = field(default_factory=lambda: schedule_definitions())
    ctx: Any = None                       # TaskContext
    params_provider: Callable[[ScheduledJob], dict] | None = None
    redis_url: str | None = None
    _last_run: dict[str, datetime] = field(default_factory=dict)
    _history: list[JobRunRecord] = field(default_factory=list)

    # ---- cron due-check ---------------------------------------------------

    def next_fire(self, job: ScheduledJob, after: datetime) -> datetime | None:
        """Next cron-triggered datetime strictly after ``after`` (UTC-aware)."""
        from croniter import croniter
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(job.timezone)
        # croniter works in the job's local tz; convert back to UTC
        after_local = after.astimezone(tz)
        itr = croniter(job.cron, after_local)
        nxt = itr.get_next(datetime)
        return nxt.astimezone(timezone.utc)

    def is_due(self, job: ScheduledJob, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        last = self._last_run.get(job.name)
        baseline = last or (now - timedelta(days=2))
        nxt = self.next_fire(job, baseline)
        return nxt is not None and now >= nxt

    # ---- execution --------------------------------------------------------

    def run_job(self, job: ScheduledJob) -> JobRunRecord:
        """Dispatch a single job with retries. Records to history."""
        from apps.worker.tasks import dispatch
        rec = JobRunRecord(
            job_name=job.name, kind=job.kind,
            started_at=datetime.now(timezone.utc),
        )
        params = self.params_provider(job) if self.params_provider else {}
        last_err: Exception | None = None
        for attempt in range(1, job.retries + 2):
            rec.attempts = attempt
            try:
                rec.result = dispatch(job.kind, self.ctx, **params)
                rec.status = "SUCCEEDED"
                rec.finished_at = datetime.now(timezone.utc)
                self._last_run[job.name] = rec.finished_at
                self._history.append(rec)
                return rec
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        rec.status = "FAILED"
        rec.error = str(last_err)
        rec.finished_at = datetime.now(timezone.utc)
        self._last_run[job.name] = rec.finished_at
        self._history.append(rec)
        return rec

    def run_due(self, now: datetime | None = None) -> list[JobRunRecord]:
        """Execute all due jobs (in-process). Returns run records."""
        now = now or datetime.now(timezone.utc)
        results: list[JobRunRecord] = []
        for job in self.jobs:
            if self.is_due(job, now):
                results.append(self.run_job(job))
        return results

    def enqueue_due(self, now: datetime | None = None) -> list[str]:
        """Enqueue due jobs onto RQ (production). Returns job IDs.

        Requires ``redis_url`` and a running ``apps.worker`` consumer.
        """
        if not self.redis_url:
            raise RuntimeError("enqueue_due requires redis_url")
        from rq import Queue
        from redis import Redis
        now = now or datetime.now(timezone.utc)
        conn = Redis.from_url(self.redis_url)
        job_ids: list[str] = []
        for job in self.jobs:
            if self.is_due(job, now):
                params = self.params_provider(job) if self.params_provider else {}
                q = Queue(job.queue, connection=conn)
                from apps.worker.tasks import dispatch
                rq_job = q.enqueue(dispatch, job.kind, self.ctx, **params,
                                   job_timeout=600, retry=job.retries)
                job_ids.append(rq_job.id)
                self._last_run[job.name] = now
        return job_ids

    # ---- introspection ----------------------------------------------------

    @property
    def history(self) -> list[JobRunRecord]:
        return list(self._history)

    def reset(self) -> None:
        """Clear run history and last-run markers (for tests)."""
        self._last_run.clear()
        self._history.clear()
