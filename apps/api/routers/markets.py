"""GET /v1/markets/* — status per market.

Reports current session state and calendar/rule versions. Values are wired
via app.state so tests can inject a deterministic provider.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from packages.common import ok, err, QuantError
from packages.common.instrument_id import Market
from packages.common.time_utils import utcnow

router = APIRouter(prefix="/v1/markets", tags=["markets"])


@router.get("/{market}/status")
async def status(market: str, request: Request):
    try:
        mkt = Market(market)
    except ValueError as e:
        return err(e)
    provider = getattr(request.app.state, "market_status_provider", None)
    if provider is None:
        # Phase-1 conservative default: report unknown session with current
        # UTC so callers know the endpoint is wired but the data source is
        # still being set up.
        return ok({
            "market": mkt.value,
            "session": "UNKNOWN",
            "as_of_utc": utcnow().isoformat(),
            "calendar_version": "v0",
            "rule_version": "v0",
        })
    try:
        payload = provider(mkt)
    except QuantError as e:
        return err(e)
    return ok(payload)
