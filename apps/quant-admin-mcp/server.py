"""quant-admin-mcp server — stdio transport over the admin/write tools.

Wraps :class:`apps.quant_admin_mcp.tools.AdminTools` (13 spec tools + 5
legacy-compat helpers) as MCP tools via FastMCP so an Agent can drive the
write/mutation surface over stdio.

The skeleton's tools.py only defined the tool *boundary*; this file adds the
runnable transport layer (mirroring apps/quant-read-mcp/server.py).

Dual control is preserved: high-risk tools (``model_request_promotion`` /
``model_approve_promotion`` / ``model_request_rollback`` /
``engage_kill_switch`` / ``release_kill_switch``) require an ``approval_id``
and ``model_approve_promotion`` additionally enforces that the second approver
differs from the requester. The server does not weaken these checks — it is a
thin transport, not a policy bypass.

Run (from repo root, with the project venv active):
    python apps/quant-admin-mcp/server.py

Backends are in-memory by default (registry + audit log); real deployments
inject DB-backed AuditLog / ModelRegistry by constructing AdminTools directly.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from packages.audit.record import AuditLog, InMemoryAuditSink
from packages.models.registry import InMemoryModelRegistry

# The package directory uses a hyphen (quant-admin-mcp) which is not a valid
# Python identifier, so we load tools.py by file path — same trick the test
# suite and the read-mcp server use. The module MUST be registered in
# sys.modules before exec so that dataclasses with KW_ONLY / field() can
# resolve ``cls.__module__`` (CPython dataclasses._is_type looks it up there).
_HERE = Path(__file__).resolve().parent
_mod_name = "_qamc_tools"
_spec = importlib.util.spec_from_file_location(_mod_name, _HERE / "tools.py")
assert _spec and _spec.loader, "could not load tools.py"
_tools_mod = importlib.util.module_from_spec(_spec)
sys.modules[_mod_name] = _tools_mod
_spec.loader.exec_module(_tools_mod)
AdminTools = _tools_mod.AdminTools


def _build_default_tools() -> AdminTools:
    """Construct AdminTools with in-memory registry + audit log.

    Real deployments inject DB-backed AuditLog / ModelRegistry. The in-memory
    defaults let the server start standalone; the dual-control and audit
    semantics are fully exercised.
    """
    reg = InMemoryModelRegistry()
    log = AuditLog(InMemoryAuditSink())
    return AdminTools(registry=reg, audit=log)


_tools = _build_default_tools()

mcp = FastMCP("quant-admin-mcp")


# ---- 1-5: long-running job creators --------------------------------------

@mcp.tool()
def ingestion_create_job(market: str, dataset: str, from_date: str,
                         to_date: str, actor: str) -> dict:
    """Queue a data-ingestion job for a (market, dataset, date_range)."""
    return _tools.ingestion_create_job(
        market=market, dataset=dataset, from_date=from_date,
        to_date=to_date, actor=actor,
    )


@mcp.tool()
def feature_create_job(dataset_snapshot_id: str, feature_set_version: str,
                       actor: str) -> dict:
    """Queue a feature-computation job pinned to a dataset snapshot."""
    return _tools.feature_create_job(
        dataset_snapshot_id=dataset_snapshot_id,
        feature_set_version=feature_set_version, actor=actor,
    )


@mcp.tool()
def dataset_create_snapshot(dataset_name: str, universe: str,
                            as_of_date: str, actor: str) -> dict:
    """Create an immutable dataset snapshot with SHA-256 identity."""
    return _tools.dataset_create_snapshot(
        dataset_name=dataset_name, universe=universe,
        as_of_date=as_of_date, actor=actor,
    )


@mcp.tool()
def backtest_create_job(model_id: str, version: str, from_date: str,
                        to_date: str, actor: str) -> dict:
    """Queue an event-driven backtest run."""
    return _tools.backtest_create_job(
        model_id=model_id, version=version,
        from_date=from_date, to_date=to_date, actor=actor,
    )


@mcp.tool()
def training_create_job(model_spec: str, dataset_snapshot_id: str,
                        actor: str) -> dict:
    """Queue a walk-forward training run for a model spec."""
    return _tools.training_create_job(
        model_spec=model_spec, dataset_snapshot_id=dataset_snapshot_id,
        actor=actor,
    )


# ---- 6: job_get_status ----------------------------------------------------

@mcp.tool()
def job_get_status(job_id: str) -> dict:
    """Return the state of a previously queued job."""
    return _tools.job_get_status(job_id=job_id)


# ---- 7: model_compare -----------------------------------------------------

@mcp.tool()
def model_compare(a_model_id: str, a_version: str,
                  b_model_id: str, b_version: str) -> dict:
    """Return side-by-side metrics for two models."""
    return _tools.model_compare(
        a_model_id=a_model_id, a_version=a_version,
        b_model_id=b_model_id, b_version=b_version,
    )


# ---- 8: model_start_shadow -----------------------------------------------

@mcp.tool()
def model_start_shadow(model_id: str, version: str, actor: str,
                       candidate_metrics: dict[str, float] | None = None,
                       baseline_metrics: dict[str, dict[str, float]] | None = None,
                       ) -> dict:
    """Promote a CANDIDATE model into SHADOW state.

    Optionally pass candidate_metrics + baseline_metrics to require the
    candidate to beat every baseline (spec §81.1 promotion gate).
    """
    return _tools.model_start_shadow(
        model_id=model_id, version=version, actor=actor,
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
    )


# ---- 9: model_request_promotion ------------------------------------------

@mcp.tool()
def model_request_promotion(model_id: str, version: str, actor: str) -> dict:
    """Open a PRODUCTION-promotion request awaiting a second approver (dual control)."""
    return _tools.model_request_promotion(
        model_id=model_id, version=version, actor=actor,
    )


# ---- 10: model_approve_promotion -----------------------------------------

@mcp.tool()
def model_approve_promotion(request_id: str, actor: str, approval_id: str,
                            candidate_metrics: dict[str, float] | None = None,
                            baseline_metrics: dict[str, dict[str, float]] | None = None,
                            ) -> dict:
    """Second approver commits the promotion; requires an actor distinct from the requester."""
    return _tools.model_approve_promotion(
        request_id=request_id, actor=actor, approval_id=approval_id,
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
    )


# ---- 11: model_request_rollback ------------------------------------------

@mcp.tool()
def model_request_rollback(market: str, horizon_days: int, actor: str,
                           approval_id: str) -> dict:
    """Retire current PRODUCTION model. Dual-control (approval_id required)."""
    return _tools.model_request_rollback(
        market=market, horizon_days=horizon_days,
        actor=actor, approval_id=approval_id,
    )


# ---- 12: risk_policy_validate --------------------------------------------

@mcp.tool()
def risk_policy_validate(policy: dict[str, Any]) -> dict:
    """Validate a proposed risk-policy map against invariants. Pure (no mutation)."""
    return _tools.risk_policy_validate(policy=policy)


# ---- 13: audit_query ------------------------------------------------------

@mcp.tool()
def audit_query(actor_id: str | None = None, resource_type: str | None = None,
                resource_id: str | None = None, limit: int = 100) -> dict:
    """Query the append-only audit log by actor / resource / time range."""
    return _tools.audit_query(
        actor_id=actor_id, resource_type=resource_type,
        resource_id=resource_id, limit=limit,
    )


# ---- legacy compat helpers (used by old callers; kept for parity) --------

@mcp.tool()
def register_model(model_id: str, version: str, market: str, horizon_days: int,
                   feature_set_hash: str, actor: str,
                   notes: str | None = None) -> dict:
    """Register a new model record in DRAFT state."""
    return _tools.register_model(
        model_id=model_id, version=version, market=market,
        horizon_days=horizon_days, feature_set_hash=feature_set_hash,
        actor=actor, notes=notes,
    )


@mcp.tool()
def promote_model(model_id: str, version: str, to_state: str, actor: str,
                 approval_id: str | None = None,
                 candidate_metrics: dict[str, float] | None = None,
                 baseline_metrics: dict[str, dict[str, float]] | None = None,
                 ) -> dict:
    """Transition a model to a target state (legacy single-call promotion)."""
    return _tools.promote_model(
        model_id=model_id, version=version, to_state=to_state,
        actor=actor, approval_id=approval_id,
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
    )


@mcp.tool()
def rollback_production(market: str, horizon_days: int, actor: str,
                        approval_id: str) -> dict:
    """Retire the current PRODUCTION model (alias of model_request_rollback)."""
    return _tools.rollback_production(
        market=market, horizon_days=horizon_days,
        actor=actor, approval_id=approval_id,
    )


@mcp.tool()
def engage_kill_switch(actor: str, approval_id: str, reason: str) -> dict:
    """Engage the global kill-switch. Requires approval_id."""
    return _tools.engage_kill_switch(
        actor=actor, approval_id=approval_id, reason=reason,
    )


@mcp.tool()
def release_kill_switch(actor: str, approval_id: str) -> dict:
    """Release the global kill-switch. Requires approval_id."""
    return _tools.release_kill_switch(actor=actor, approval_id=approval_id)


def main() -> None:
    """Run the MCP server on stdio."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
