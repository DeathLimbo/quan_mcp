"""GET /v1/admin/scheduler/jobs — scheduled job catalog (spec §92).

Read-only surface exposing the canonical schedule defined in
:mod:`apps.scheduler.schedule`. The endpoint returns the flat list plus a
per-job Airflow / n8n rendering so ops can point their orchestrator at a
single deterministic source of truth.

Adding, removing, or editing jobs is a code change — this API deliberately
does NOT accept mutations.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from apps.scheduler import (
    as_dict, render_airflow, render_n8n, schedule_definitions,
)
from packages.common import err, ok

router = APIRouter(prefix="/v1/admin/scheduler", tags=["admin"])


@router.get("/jobs")
async def list_jobs(
    format: str = Query("dict", pattern="^(dict|airflow|n8n)$",
                        description="dict|airflow|n8n"),
):
    jobs = schedule_definitions()
    if format == "airflow":
        payload = [render_airflow(j) for j in jobs]
    elif format == "n8n":
        payload = [render_n8n(j) for j in jobs]
    else:
        payload = [as_dict(j) for j in jobs]
    return ok({"count": len(jobs), "jobs": payload, "format": format})


@router.get("/jobs/{name}")
async def get_job(
    name: str,
    format: str = Query("dict", pattern="^(dict|airflow|n8n)$"),
):
    for j in schedule_definitions():
        if j.name == name:
            if format == "airflow":
                return ok(render_airflow(j))
            if format == "n8n":
                return ok(render_n8n(j))
            return ok(as_dict(j))
    return err(KeyError(f"scheduled job '{name}' not found"))
