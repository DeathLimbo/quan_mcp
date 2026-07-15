"""Canonical response envelope used by every API and MCP tool."""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from packages.common.errors import ErrorCode, QuantError

T = TypeVar("T")


class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiResponse(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ErrorInfo | None = None
    trace_id: str | None = None
    request_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


def ok(
    data: Any = None,
    *,
    trace_id: str | None = None,
    request_id: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "trace_id": trace_id,
        "request_id": request_id,
        "warnings": warnings or [],
    }


def err(
    exc: QuantError | Exception,
    *,
    trace_id: str | None = None,
    request_id: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    if isinstance(exc, QuantError):
        payload = exc.to_dict()
    else:
        payload = {"code": ErrorCode.INTERNAL_ERROR.value, "message": str(exc), "details": {}}
    return {
        "ok": False,
        "data": None,
        "error": payload,
        "trace_id": trace_id,
        "request_id": request_id,
        "warnings": warnings or [],
    }
