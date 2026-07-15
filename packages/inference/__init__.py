"""Inference: PIT-safe scoring with fail-closed NO_FORECAST semantics.

The service returns a ``Forecast`` when all inputs are available and clean at
``as_of``; otherwise it returns a structured ``NoForecast`` reason so the
downstream risk engine can enforce NO_TRADE. Nothing here silently imputes.
"""
from packages.inference.service import (
    Forecast, InferenceService, NoForecast, NoForecastReason,
)

__all__ = ["Forecast", "InferenceService", "NoForecast", "NoForecastReason"]
