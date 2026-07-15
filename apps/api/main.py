"""FastAPI monolith (module-partitioned).

Routers are registered per bounded context. Phase 0 provides:
- /v1/health   liveness / readiness
- /metrics     Prometheus metrics
- middleware:  trace_id/request_id injection, error envelope, JSON logging
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from packages.common import (
    QuantError,
    bind_trace,
    configure_logging,
    err,
    get_logger,
    ok,
    utcnow,
)

logger = get_logger("api")

REQ_COUNTER = Counter("api_requests_total", "API requests", ["path", "status"])
REQ_LATENCY = Histogram("api_request_seconds", "API latency", ["path"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("api.startup", env=os.getenv("APP_ENV", "dev"))
    yield
    logger.info("api.shutdown")


app = FastAPI(
    title="cross-market-quant API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def trace_and_envelope(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    bind_trace(trace_id=trace_id, request_id=request_id)
    start = utcnow()
    try:
        response = await call_next(request)
    except QuantError as e:
        logger.warning("api.quant_error", code=e.code.value, path=request.url.path)
        REQ_COUNTER.labels(request.url.path, "error").inc()
        return JSONResponse(status_code=400, content=err(e, trace_id=trace_id, request_id=request_id))
    except Exception as e:  # noqa: BLE001
        logger.exception("api.internal_error", path=request.url.path)
        REQ_COUNTER.labels(request.url.path, "500").inc()
        return JSONResponse(status_code=500, content=err(e, trace_id=trace_id, request_id=request_id))
    elapsed = (utcnow() - start).total_seconds()
    REQ_LATENCY.labels(request.url.path).observe(elapsed)
    REQ_COUNTER.labels(request.url.path, str(response.status_code)).inc()
    response.headers["x-trace-id"] = trace_id
    response.headers["x-request-id"] = request_id
    return response


from apps.api.routers import (
    admin_ingestion_router,
    data_status_router,
    forecast_router,
    fundamentals_router,
    instruments_router,
    markets_router,
    portfolio_router,
    scheduler_router,
)

app.include_router(instruments_router)
app.include_router(markets_router)
app.include_router(forecast_router)
app.include_router(portfolio_router)
app.include_router(data_status_router)
app.include_router(fundamentals_router)
app.include_router(admin_ingestion_router)
app.include_router(scheduler_router)


@app.get("/v1/health")
async def health():
    return ok({"status": "ok", "utcnow": utcnow().isoformat()})


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
