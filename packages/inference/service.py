"""Inference service.

Contract:
- Input: ``instrument_id``, ``as_of`` (UTC), ``bars`` (all available prior bars).
- Output: ``Forecast`` or ``NoForecast`` (fail-closed reason).
- No silent imputation. No look-ahead. Feature hash must match the model's
  training feature-set hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from packages.common.instrument_id import InstrumentId
from packages.common.time_utils import ensure_utc
from packages.data_sources.contracts import Bar
from packages.features.featureset import FeatureSet
from packages.features.registry import FundamentalContext
from packages.models.base import Model, Prediction
from packages.models.registry import InMemoryModelRegistry


class NoForecastReason(str, Enum):
    MISSING_FEATURE = "missing_feature"
    NO_PRODUCTION_MODEL = "no_production_model"
    NO_ARTIFACT = "no_artifact"
    FEATURE_HASH_MISMATCH = "feature_hash_mismatch"
    INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True, slots=True)
class NoForecast:
    instrument_id: InstrumentId
    as_of: datetime
    reason: NoForecastReason
    detail: str


@dataclass(frozen=True, slots=True)
class Forecast:
    instrument_id: InstrumentId
    as_of: datetime
    horizon_days: int
    score: float
    model_id: str
    model_version: str
    feature_hash: str


class InferenceService:
    """Bind a registry + FeatureSet; emit Forecast/NoForecast per instrument."""

    def __init__(self, registry: InMemoryModelRegistry, featureset: FeatureSet) -> None:
        self._registry = registry
        self._fs = featureset

    def score(
        self,
        *,
        instrument_id: InstrumentId,
        as_of: datetime,
        horizon_days: int,
        bars: Sequence[Bar],
        fund_ctx: FundamentalContext | None = None,
    ) -> Forecast | NoForecast:
        as_of_utc = ensure_utc(as_of)
        rec = self._registry.get_production(instrument_id.market, horizon_days)
        if rec is None:
            return NoForecast(
                instrument_id=instrument_id, as_of=as_of_utc,
                reason=NoForecastReason.NO_PRODUCTION_MODEL,
                detail=f"no PRODUCTION model for market={instrument_id.market.value} horizon={horizon_days}",
            )
        if rec.feature_set_hash != self._fs.content_hash:
            return NoForecast(
                instrument_id=instrument_id, as_of=as_of_utc,
                reason=NoForecastReason.FEATURE_HASH_MISMATCH,
                detail=f"expected={rec.feature_set_hash} got={self._fs.content_hash}",
            )
        artifact = self._registry.get_artifact(rec.model_id, rec.version)
        if artifact is None:
            return NoForecast(
                instrument_id=instrument_id, as_of=as_of_utc,
                reason=NoForecastReason.NO_ARTIFACT,
                detail=f"artifact missing for {rec.model_id}@{rec.version}",
            )
        if not isinstance(artifact, Model):
            # duck-typed Protocol check via runtime_checkable
            return NoForecast(
                instrument_id=instrument_id, as_of=as_of_utc,
                reason=NoForecastReason.NO_ARTIFACT,
                detail=f"artifact for {rec.model_id}@{rec.version} does not satisfy Model protocol",
            )

        feats = self._fs.compute(bars, as_of_utc, fund_ctx=fund_ctx)
        missing = [k for k, v in feats.items() if v is None]
        if missing:
            return NoForecast(
                instrument_id=instrument_id, as_of=as_of_utc,
                reason=NoForecastReason.MISSING_FEATURE,
                detail=f"missing features: {sorted(missing)}",
            )

        pred: Prediction = artifact.predict_one(feats)
        return Forecast(
            instrument_id=instrument_id,
            as_of=as_of_utc,
            horizon_days=horizon_days,
            score=pred.score,
            model_id=rec.model_id,
            model_version=rec.version,
            feature_hash=self._fs.content_hash,
        )
