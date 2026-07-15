"""GET /v1/portfolio/{portfolio_id}/snapshot — read-only portfolio view.

Data source is ``app.state.portfolio_snapshot_provider``. Router never
computes weights; it just serializes what the provider returns.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from packages.common import ok, err
from packages.common.errors import DataNotReadyError, QuantError

router = APIRouter(prefix="/v1/portfolio", tags=["portfolio"])


@router.get("/{portfolio_id}/snapshot")
async def snapshot(portfolio_id: str, request: Request):
    provider = getattr(request.app.state, "portfolio_snapshot_provider", None)
    if provider is None:
        return err(DataNotReadyError("no portfolio provider registered",
                                     details={"reason": "NO_PORTFOLIO_SNAPSHOT_PROVIDER"}))
    try:
        payload = provider(portfolio_id)
    except QuantError as e:
        return err(e)
    return ok(payload)
