"""POST /v1/admin/ingestion/jobs — trigger a bar ingestion job (spec §1.57).

Runs ``ingest_bars_daily`` synchronously against the adapter registry
mounted on ``app.state.adapter_registry``. The response echoes the full
:class:`IngestReport` in JSON so callers can see:
- how many bars were written,
- how many were skipped by the watermark,
- every DQ finding (severity + rule + reference),
- whether the write was blocked by fail-closed DQ.

The router is admin-only in production (auth is enforced at the reverse
proxy today; V1 permission scope is tracked in spec §68).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from packages.common import err, ok
from packages.common.errors import (
    DataConflictError, DataNotReadyError, QuantError,
)
from packages.common.instrument_id import parse_instrument_id
from packages.ingestion.pipeline import (
    IngestReport, SqlBarSink, ingest_bars_daily,
)

router = APIRouter(prefix="/v1/admin/ingestion", tags=["admin"])


class IngestionJobRequest(BaseModel):
    source: str = Field(..., description="adapter_id, e.g. 'akshare' or 'yfinance'")
    instrument_id: str = Field(..., description="canonical id")
    start: date
    end: date
    strict: bool = Field(True, description="fail-closed on DQ ERROR/CRITICAL")


def _finding_dict(f) -> dict:
    return {
        "rule": f.rule,
        "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
        "message": f.message,
        "reference": f.reference,
    }


def _report_dict(r: IngestReport) -> dict:
    return {
        "instrument_id": r.instrument_id.canonical(),
        "source": r.source,
        "written": r.written,
        "skipped_by_watermark": r.skipped_by_watermark,
        "dq_blocked": r.dq_blocked,
        "watermark_before": r.watermark_before.isoformat() if r.watermark_before else None,
        "watermark_after": r.watermark_after.isoformat() if r.watermark_after else None,
        "findings": [_finding_dict(f) for f in r.findings],
    }


@router.post("/jobs")
async def create_job(req: IngestionJobRequest, request: Request):
    registry = getattr(request.app.state, "adapter_registry", None)
    if registry is None:
        return err(DataNotReadyError("adapter_registry not registered",
                                     details={"reason": "NO_ADAPTER_REGISTRY"}))
    watermarks = getattr(request.app.state, "watermarks", None)
    if watermarks is None:
        return err(DataNotReadyError("watermarks store not registered",
                                     details={"reason": "NO_WATERMARKS"}))
    bar_repo = getattr(request.app.state, "bar_repo", None)

    try:
        iid = parse_instrument_id(req.instrument_id)
    except (ValueError, QuantError) as e:
        return err(e)

    try:
        adapter = registry.get(req.source)
    except (KeyError, QuantError) as e:
        return err(DataNotReadyError(f"unknown adapter '{req.source}'",
                                     details={"source": req.source}))

    if req.start > req.end:
        return err(ValueError("start must be <= end"))

    sink = SqlBarSink(bar_repo) if bar_repo is not None else None
    try:
        report = ingest_bars_daily(
            adapter, iid, req.start, req.end,
            watermarks=watermarks, sink=sink, strict=req.strict,
        )
    except DataConflictError as e:
        return err(e)
    except QuantError as e:
        return err(e)

    return ok(_report_dict(report))
