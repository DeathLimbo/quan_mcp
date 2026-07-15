"""Tests for :mod:`apps.scheduler` and the /v1/admin/scheduler API surface."""
from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.scheduler import (
    ScheduledJob, as_dict, render_airflow, render_n8n, schedule_definitions,
)

CLIENT = TestClient(app)


def test_schedule_definitions_are_non_empty_and_unique():
    jobs = schedule_definitions()
    assert len(jobs) >= 8, "spec §92 requires a full ingest+forecast+eval loop"
    names = [j.name for j in jobs]
    assert len(set(names)) == len(names), "job names must be unique"
    # Each job carries a real cron and a real IANA timezone.
    for j in jobs:
        assert j.cron.strip()
        assert j.timezone in {"UTC", "Asia/Shanghai", "America/New_York"}


def test_dependencies_reference_known_jobs():
    jobs = schedule_definitions()
    known = {j.name for j in jobs}
    for j in jobs:
        for dep in j.depends_on:
            assert dep in known, f"{j.name} depends on unknown {dep}"


def test_render_airflow_contains_required_fields():
    job = schedule_definitions()[0]
    rendered = render_airflow(job)
    assert rendered["dag_id"].startswith("quant-")
    assert rendered["schedule_interval"] == job.cron
    assert rendered["timezone"] == job.timezone
    assert rendered["default_args"]["retries"] == job.retries


def test_render_n8n_contains_cron_node():
    job = schedule_definitions()[0]
    rendered = render_n8n(job)
    node_types = {n["type"] for n in rendered["nodes"]}
    assert "n8n-nodes-base.cron" in node_types
    assert rendered["settings"]["timezone"] == job.timezone


def test_as_dict_is_json_serializable_with_list_deps():
    j = ScheduledJob(name="x", kind="run_forecast", cron="* * * * *",
                     timezone="UTC", depends_on=("a", "b"))
    d = as_dict(j)
    assert d["depends_on"] == ["a", "b"]
    # dataclass fields are all primitive types after asdict.
    import json
    json.dumps(d)


def test_api_lists_jobs_in_dict_format():
    r = CLIENT.get("/v1/admin/scheduler/jobs")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["format"] == "dict"
    assert body["data"]["count"] == len(body["data"]["jobs"])
    assert body["data"]["count"] >= 8


def test_api_lists_jobs_in_airflow_format():
    r = CLIENT.get("/v1/admin/scheduler/jobs?format=airflow")
    body = r.json()
    assert body["ok"] is True
    for entry in body["data"]["jobs"]:
        assert "dag_id" in entry
        assert "schedule_interval" in entry


def test_api_get_single_job_by_name():
    first_name = schedule_definitions()[0].name
    r = CLIENT.get(f"/v1/admin/scheduler/jobs/{first_name}")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["name"] == first_name


def test_api_get_unknown_job_returns_error_envelope():
    r = CLIENT.get("/v1/admin/scheduler/jobs/does_not_exist")
    body = r.json()
    assert body["ok"] is False
    assert body["error"] is not None


def test_api_rejects_bad_format_param():
    r = CLIENT.get("/v1/admin/scheduler/jobs?format=yaml")
    # FastAPI query pattern rejects with 422.
    assert r.status_code == 422
