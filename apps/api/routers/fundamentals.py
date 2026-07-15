"""GET /v1/fundamentals/facts — PIT fundamental fact reader (spec §1.59).

Returns the latest fact whose ``available_at_utc <= as_of`` per requested
``fact_name``. If a name has no fact visible by ``as_of``, the entry is
elided (rather than filled with a stale surrogate) — callers know exactly
which facts are missing.

Backend: :class:`SqlFundamentalFactRepository` (or any object with the
``get_as_of(instrument_id, name, as_of) -> Fact | None`` method) mounted
on ``app.state.fact_repo``. Test suites inject an in-memory FactStore
adapter with the same interface.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request

from packages.common import err, ok
from packages.common.errors import DataNotReadyError, QuantError
from packages.common.instrument_id import parse_instrument_id
from packages.common.time_utils import ensure_utc, utcnow
from packages.fundamentals.facts import FactName

router = APIRouter(prefix="/v1/fundamentals", tags=["fundamentals"])


@router.get("/facts")
async def facts(
    request: Request,
    instrument_id: str = Query(..., description="canonical instrument id"),
    as_of: str | None = Query(
        None, description="ISO-8601 UTC point-in-time; defaults to now"),
    fact_names: str = Query(
        ..., description="comma-separated FactName values (revenue,eps,...)"),
):
    repo = getattr(request.app.state, "fact_repo", None)
    if repo is None:
        return err(DataNotReadyError("fact_repo not registered",
                                     details={"reason": "NO_FACT_REPO"}))
    try:
        iid = parse_instrument_id(instrument_id)
    except (ValueError, QuantError) as e:
        return err(e)

    try:
        if as_of:
            parsed = datetime.fromisoformat(as_of)
            # Be permissive: treat naive input as UTC (documented in query docstring).
            if parsed.tzinfo is None:
                from datetime import timezone as _tz
                parsed = parsed.replace(tzinfo=_tz.utc)
            as_of_dt = ensure_utc(parsed)
        else:
            as_of_dt = utcnow()
    except ValueError as e:
        return err(e)

    names: list[FactName] = []
    for raw in (n.strip() for n in fact_names.split(",") if n.strip()):
        try:
            names.append(FactName(raw))
        except ValueError as e:
            return err(e)
    if not names:
        return err(ValueError("fact_names must contain at least one entry"))

    payload: list[dict] = []
    for name in names:
        try:
            f = repo.get_as_of(iid, name, as_of_dt)
        except QuantError as e:
            return err(e)
        if f is None:
            continue
        payload.append({
            "fact_name": f.name.value,
            "value": str(f.value),
            "currency": f.currency,
            "period_end": f.period_end.isoformat(),
            "as_of_utc": f.as_of_utc.isoformat(),
            "available_at_utc": f.available_at_utc.isoformat(),
            "source": f.source,
        })

    return ok({
        "instrument_id": iid.canonical(),
        "as_of_utc": as_of_dt.isoformat(),
        "facts": payload,
        "requested": [n.value for n in names],
        "missing": sorted(set(n.value for n in names)
                          - set(p["fact_name"] for p in payload)),
    })
