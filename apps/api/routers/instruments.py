"""GET /v1/instruments/* — resolve / describe instruments.

Read-only. Writes go through packages.instruments.service via admin MCP.
The service is injected via ``get_instrument_service`` so tests can swap
in an isolated repository.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from apps.api.deps import get_instrument_service
from packages.common import QuantError, err, ok
from packages.common.errors import UnknownInstrumentError
from packages.common.instrument_id import parse_instrument_id
from packages.instruments.service import InstrumentService

router = APIRouter(prefix="/v1/instruments", tags=["instruments"])


def _describe(rec) -> dict:
    d = rec.descriptor
    iid = d.instrument_id
    return {
        "instrument_id": iid.canonical(),
        "market": iid.market.value,
        "asset_type": iid.asset_type.value,
        "venue": iid.venue.value,
        "symbol": iid.symbol,
        "name_local": d.name_local,
        "name_en": d.name_en,
        "currency": d.currency,
        "lot_size": d.lot_size,
        "status": d.status,
        "ingested_at_utc": rec.ingested_at_utc.isoformat()
            if rec.ingested_at_utc else None,
        "calendar_version": rec.calendar_version,
        "rule_version": rec.rule_version,
    }


@router.get("/resolve")
async def resolve(
    q: str = Query(..., min_length=1, description="canonical id or alias"),
    source: str = Query("canonical", description="alias source (bloomberg/refinitiv/...)"),
    svc: InstrumentService = Depends(get_instrument_service),
):
    # Fast path: canonical parse succeeds → return identity even if not yet
    # in the store (Phase-1 partial bootstrap).
    try:
        iid = parse_instrument_id(q)
        rec = svc._repo.get(iid)  # type: ignore[attr-defined]
        if rec is not None:
            return ok(_describe(rec))
        return ok({"instrument_id": iid.canonical(), "market": iid.market.value,
                   "asset_type": iid.asset_type.value, "venue": iid.venue.value,
                   "symbol": iid.symbol, "known": False})
    except ValueError:
        pass
    # Alias fallback.
    try:
        rec = svc.resolve(q, source=source)
        return ok(_describe(rec))
    except (UnknownInstrumentError, QuantError) as e:
        return err(e)


@router.get("/{instrument_id:path}")
async def get(instrument_id: str,
              svc: InstrumentService = Depends(get_instrument_service)):
    try:
        iid = parse_instrument_id(instrument_id)
    except (ValueError, QuantError) as e:
        return err(e)
    rec = svc._repo.get(iid)  # type: ignore[attr-defined]
    if rec is None:
        return ok({"instrument_id": iid.canonical(), "known": False,
                   "note": "instrument not yet registered"})
    return ok(_describe(rec))
