"""POST /v1/forecast/run — invoke inference service to produce forecasts.

The router is thin: it validates the request body, defers to the inference
service registered on ``app.state.inference_service``, and always returns
the standard ``ok/err`` envelope. Fail-closed contracts live inside the
inference service — this router never fabricates a forecast.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from packages.common import ok, err
from packages.common.errors import DataNotReadyError, QuantError
from packages.common.instrument_id import Market, parse_instrument_id

router = APIRouter(prefix="/v1/forecast", tags=["forecast"])


class ForecastRequest(BaseModel):
    market: str
    horizon_days: int = Field(gt=0, le=250)
    instruments: list[str] = Field(default_factory=list)


@router.post("/run")
async def run(req: ForecastRequest, request: Request):
    svc = getattr(request.app.state, "inference_service", None)
    if svc is None:
        return err(DataNotReadyError("inference_service not registered",
                                     details={"reason": "NO_INFERENCE_SERVICE"}))
    try:
        market = Market(req.market)
    except ValueError as e:
        return err(e)
    try:
        iids = [parse_instrument_id(x) for x in req.instruments]
    except (ValueError, QuantError) as e:
        return err(e)

    try:
        forecasts, no_forecasts = svc.run(market=market, horizon_days=req.horizon_days,
                                          instruments=iids)
    except QuantError as e:
        return err(e)

    def _f(fc: Any) -> dict[str, Any]:
        return {
            "instrument_id": fc.instrument_id.canonical(),
            "score": fc.score,
            "horizon_days": fc.horizon_days,
            "model_id": fc.model_id,
            "model_version": fc.model_version,
            "feature_hash": fc.feature_hash,
        }

    def _nf(nf: Any) -> dict[str, Any]:
        return {
            "instrument_id": nf.instrument_id.canonical(),
            "reason": nf.reason.value if hasattr(nf.reason, "value") else str(nf.reason),
            "detail": nf.detail,
        }

    return ok({
        "forecasts": [_f(x) for x in forecasts],
        "no_forecasts": [_nf(x) for x in no_forecasts],
    })
