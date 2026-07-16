"""Inference service.

Contract:
- Input: ``instrument_id``, ``as_of`` (UTC), ``bars`` (all available prior bars).
- Output: ``Forecast`` or ``NoForecast`` (fail-closed reason).
- No silent imputation. No look-ahead. Feature hash must match the model's
  training feature-set hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Sequence

from packages.common.instrument_id import InstrumentId
from packages.common.time_utils import ensure_utc
from packages.data_sources.contracts import Bar
from packages.features.featureset import FeatureSet
from packages.features.registry import FundamentalContext
from packages.fx.converter import FxConverter, FxNotAvailableError
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
    # Cross-currency decomposition (spec §12.6). ``score`` stays the headline
    # local-currency expectation for backward compat; these three fields split
    # it so downstream portfolio/eval can attribute in the base currency.
    # ``expected_fx_return`` is the *realised* FX contribution used for
    # attribution only — NOT an FX forecast (spec §12.6 forbids packaging FX
    # point forecasts as high-confidence unless a separate FX model passes
    # validation). ``None`` when the instrument is base-currency or no
    # FxConverter is wired.
    expected_return_local: float | None = None
    expected_fx_return: float | None = None
    expected_return_base: float | None = None
    # Provenance (spec §38 数据: 每条预测可追溯到数据/日历/规则版本).
    # Stamped from the latest input Bar so a forecast can be audited back to
    # the exact data source version, calendar version and rule version that
    # produced it.
    data_version: str | None = None
    calendar_version: str | None = None
    rule_version: str | None = None


class InferenceService:
    """Bind a registry + FeatureSet; emit Forecast/NoForecast per instrument."""

    def __init__(
        self,
        registry: InMemoryModelRegistry,
        featureset: FeatureSet,
        *,
        fx_converter: FxConverter | None = None,
        base_ccy: str = "CNY",
    ) -> None:
        self._registry = registry
        self._fs = featureset
        self._fx = fx_converter
        self._base_ccy = base_ccy

    def score(
        self,
        *,
        instrument_id: InstrumentId,
        as_of: datetime,
        horizon_days: int,
        bars: Sequence[Bar],
        fund_ctx: FundamentalContext | None = None,
        instrument_ccy: str | None = None,
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
        local_ret = float(pred.score)
        # Cross-currency attribution (spec §12.6). For a non-base-currency
        # instrument, split the headline score into local + realised FX +
        # base. Conservative: FX is realised history only, never a forecast.
        # Fail-closed: if the FX rate is missing we leave base==local rather
        # than fabricating a conversion (spec §38 风控).
        fx_ret: float | None = None
        base_ret = local_ret
        if (
            self._fx is not None
            and instrument_ccy is not None
            and instrument_ccy != self._base_ccy
        ):
            start = as_of_utc.date() - timedelta(days=horizon_days)
            end = as_of_utc.date()
            try:
                fx_ret = float(self._fx.fx_return(
                    local_ccy=instrument_ccy, start=start, end=end))
                base_ret = local_ret + fx_ret
            except FxNotAvailableError:
                fx_ret = None
                base_ret = local_ret
        return Forecast(
            instrument_id=instrument_id,
            as_of=as_of_utc,
            horizon_days=horizon_days,
            score=local_ret,
            model_id=rec.model_id,
            model_version=rec.version,
            feature_hash=self._fs.content_hash,
            expected_return_local=local_ret,
            expected_fx_return=fx_ret,
            expected_return_base=base_ret,
            data_version=bars[-1].source_version if bars else None,
            calendar_version=bars[-1].calendar_version if bars else None,
            rule_version=bars[-1].rule_version if bars else None,
        )
