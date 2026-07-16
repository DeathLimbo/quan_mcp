"""Admin MCP durable job tests (issue #3).

AdminTools with a wired SqlJobStore must survive a simulated restart: a new
AdminTools instance backed by the same engine recovers job state. Without a
job_store it falls back to in-memory (backcompat).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa

from packages.audit.record import AuditLog, InMemoryAuditSink
from packages.models.registry import InMemoryModelRegistry
from packages.persistence import SqlJobStore
from packages.persistence.repositories import _metadata, admin_job_t

_ADMIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps" / "quant-admin-mcp" / "tools.py"
)

spec = importlib.util.spec_from_file_location("qat_admin_durable", _ADMIN_PATH)
assert spec and spec.loader
_admin = importlib.util.module_from_spec(spec)
sys.modules["qat_admin_durable"] = _admin
spec.loader.exec_module(_admin)
AdminTools = _admin.AdminTools


def _engine():
    eng = sa.create_engine("sqlite:///:memory:")
    _metadata.create_all(eng, tables=[admin_job_t])
    return eng


def test_admin_job_survives_restart():
    eng = _engine()
    admin = AdminTools(
        registry=InMemoryModelRegistry(),
        audit=AuditLog(InMemoryAuditSink()),
        job_store=SqlJobStore(eng))
    r = admin.ingestion_create_job(
        market="US", dataset="bars", from_date="2026-01-01",
        to_date="2026-07-01", actor="ops")
    jid = r["data"]["job_id"]
    assert r["data"]["status"] == "QUEUED"

    # issue #3: simulate restart — new AdminTools, same DB, empty in-memory dict
    admin2 = AdminTools(
        registry=InMemoryModelRegistry(),
        audit=AuditLog(InMemoryAuditSink()),
        job_store=SqlJobStore(eng))
    r2 = admin2.job_get_status(job_id=jid)
    assert r2["ok"] is True
    assert r2["data"]["job_id"] == jid
    assert r2["data"]["status"] == "QUEUED"  # recovered from durable store


def test_admin_job_status_update_persists():
    eng = _engine()
    store = SqlJobStore(eng)
    admin = AdminTools(
        registry=InMemoryModelRegistry(),
        audit=AuditLog(InMemoryAuditSink()), job_store=store)
    jid = admin.ingestion_create_job(
        market="US", dataset="bars", from_date="2026-01-01",
        to_date="2026-07-01", actor="ops")["data"]["job_id"]
    # ops flips the job to SUCCESS via the helper
    admin._mark_job(jid, _admin.JobStatus.SUCCEEDED, result={"rows": 42})

    # new instance sees the updated status
    admin2 = AdminTools(
        registry=InMemoryModelRegistry(),
        audit=AuditLog(InMemoryAuditSink()), job_store=SqlJobStore(eng))
    r = admin2.job_get_status(job_id=jid)
    assert r["data"]["status"] == "SUCCEEDED"


def test_admin_job_backcompat_without_store():
    # no job_store → in-memory only (existing behaviour preserved)
    admin = AdminTools(
        registry=InMemoryModelRegistry(), audit=AuditLog(InMemoryAuditSink()))
    r = admin.ingestion_create_job(
        market="US", dataset="bars", from_date="2026-01-01",
        to_date="2026-07-01", actor="ops")
    assert r["ok"] is True
    jid = r["data"]["job_id"]
    assert admin.job_get_status(job_id=jid)["data"]["status"] == "QUEUED"
