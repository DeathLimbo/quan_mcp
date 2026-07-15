"""Unified error hierarchy. Every API/MCP response maps to one of these codes.

Failure ledger (fail-closed):
- Data:     DATA_NOT_READY, DATA_STALE, DATA_CONFLICT
- Universe: UNKNOWN_INSTRUMENT, UNSUPPORTED_ASSET, UNSUPPORTED_HORIZON
- Session:  MARKET_CLOSED
- Model:    MODEL_NOT_AVAILABLE, MODEL_NOT_APPROVED, FEATURE_MISSING, OUT_OF_DISTRIBUTION
- Risk:     RISK_REJECTED
- AuthZ:    PERMISSION_DENIED
- Ops:      JOB_ALREADY_RUNNING, INTERNAL_ERROR
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    DATA_NOT_READY = "DATA_NOT_READY"
    DATA_STALE = "DATA_STALE"
    DATA_CONFLICT = "DATA_CONFLICT"
    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"
    UNSUPPORTED_ASSET = "UNSUPPORTED_ASSET"
    UNSUPPORTED_HORIZON = "UNSUPPORTED_HORIZON"
    MARKET_CLOSED = "MARKET_CLOSED"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    MODEL_NOT_APPROVED = "MODEL_NOT_APPROVED"
    FEATURE_MISSING = "FEATURE_MISSING"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
    RISK_REJECTED = "RISK_REJECTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    JOB_ALREADY_RUNNING = "JOB_ALREADY_RUNNING"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class QuantError(Exception):
    """Base class. Every subclass carries a stable machine code."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None):
        super().__init__(message or self.code.value)
        self.message = message or self.code.value
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}


def _mk(code: ErrorCode):
    return type(
        code.name.title().replace("_", "") + "Error",
        (QuantError,),
        {"code": code},
    )


DataNotReadyError = _mk(ErrorCode.DATA_NOT_READY)
DataStaleError = _mk(ErrorCode.DATA_STALE)
DataConflictError = _mk(ErrorCode.DATA_CONFLICT)
UnknownInstrumentError = _mk(ErrorCode.UNKNOWN_INSTRUMENT)
UnsupportedAssetError = _mk(ErrorCode.UNSUPPORTED_ASSET)
UnsupportedHorizonError = _mk(ErrorCode.UNSUPPORTED_HORIZON)
MarketClosedError = _mk(ErrorCode.MARKET_CLOSED)
ModelNotAvailableError = _mk(ErrorCode.MODEL_NOT_AVAILABLE)
ModelNotApprovedError = _mk(ErrorCode.MODEL_NOT_APPROVED)
FeatureMissingError = _mk(ErrorCode.FEATURE_MISSING)
OutOfDistributionError = _mk(ErrorCode.OUT_OF_DISTRIBUTION)
RiskRejectedError = _mk(ErrorCode.RISK_REJECTED)
PermissionDeniedError = _mk(ErrorCode.PERMISSION_DENIED)
JobAlreadyRunningError = _mk(ErrorCode.JOB_ALREADY_RUNNING)
InternalError = _mk(ErrorCode.INTERNAL_ERROR)
