"""GET /v1/data/status — bar watermark surface (spec §1.58).

Reports the point-in-time state of the daily-bar store for a given
``instrument_id``:
- ``latest_event_time``    — most recent bar's market-local event time
- ``latest_available_at``  — most recent ``available_at_utc`` (ingest cutoff)
- ``quality_status``       — coarse OK / STALE / EMPTY signal
- ``source``               — echoes filter if provided

The router reads through :class:`SqlBarRepository` mounted on
``app.state.bar_repo``. Tests inject an in-memory repository via the same
attribute so no DB is required.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Query, Request

from packages.common import err, ok
from packages.common.errors import DataNotReadyError, QuantError
from packages.common.instrument_id import parse_instrument_id
from packages.common.time_utils import utcnow

router = APIRouter(prefix="/v1/data", tags=["data"])


@router.get("/status")
async def status(
    request: Request,
    instrument_id: str = Query(..., description="canonical instrument id"),
    source: str | None = Query(None, description="optional source filter"),
    stale_after_hours: int = Query(48, ge=1, le=24 * 30,
                                   description="STALE threshold in hours"),
):
    repo = getattr(request.app.state, "bar_repo", None)
    if repo is None:
        return err(DataNotReadyError("bar_repo not registered",
                                     details={"reason": "NO_BAR_REPO"}))
    try:
        iid = parse_instrument_id(instrument_id)
    except (ValueError, QuantError) as e:
        return err(e)
    try:
        latest = repo.latest(iid, source=source)
    except QuantError as e:
        return err(e)

    if latest is None:
        return ok({
            "instrument_id": iid.canonical(),
            "source": source,
            "quality_status": "EMPTY",
            "latest_event_time": None,
            "latest_available_at": None,
            "latest_market_local_date": None,
            "as_of_utc": utcnow().isoformat(),
        })

    now = utcnow()
    age = now - latest.available_at_utc
    status_code = "OK" if age <= timedelta(hours=stale_after_hours) else "STALE"
    return ok({
        "instrument_id": iid.canonical(),
        "source": source or latest.source,
        "quality_status": status_code,
        "latest_event_time": latest.event_time_utc.isoformat(),
        "latest_available_at": latest.available_at_utc.isoformat(),
        "latest_market_local_date": latest.market_local_date.isoformat(),
        "as_of_utc": now.isoformat(),
        "calendar_version": latest.calendar_version,
        "rule_version": latest.rule_version,
    })
