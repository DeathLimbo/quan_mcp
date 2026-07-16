"""ForecastService — orchestrator that adapts InferenceService.score() to the
batch ``run()`` contract expected by the FastAPI / MCP surface (issue #5).

The core :class:`InferenceService` scores a single instrument. Routers and the
MCP forecast path need a batch entrypoint that looks up PIT bars per
instrument, calls ``score()``, and aggregates Forecast / NoForecast results.
This orchestrator does exactly that without duplicating inference logic.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, Sequence

from packages.common.instrument_id import InstrumentId, Market
from packages.common.time_utils import ensure_utc
from packages.data_sources.contracts import Bar
from packages.inference.service import (
    Forecast, InferenceService, NoForecast,
)

# Bar lookup signature mirrors quant-read-mcp BarLookup (as_of_utc is a kwarg
# used for PIT filtering, issue #1).
BarLookup = Callable[..., list[Bar]]


class ForecastService:
    """Batch forecast orchestrator wrapping :class:`InferenceService`.

    ``run`` is the contract the FastAPI forecast router and the MCP
    ``forecast_run`` / ``screen_run`` paths expect: given a market, horizon
    and instrument list, return ``(forecasts, no_forecasts)``.
    """

    def __init__(
        self,
        inference: InferenceService,
        bar_lookup: BarLookup,
    ) -> None:
        self._inference = inference
        self._bar_lookup = bar_lookup

    def run(
        self,
        *,
        market: Market,
        horizon_days: int,
        instruments: Sequence[InstrumentId],
        as_of: datetime | None = None,
    ) -> tuple[list[Forecast], list[NoForecast]]:
        as_of_utc = ensure_utc(as_of) if as_of is not None else datetime.now(
            timezone.utc)
        forecasts: list[Forecast] = []
        no_forecasts: list[NoForecast] = []
        for iid in instruments:
            # PIT-safe bar lookup (issue #1): forward as_of_utc so the data
            # layer can filter available_at_utc <= as_of.
            try:
                bars = self._bar_lookup(
                    iid, date(1970, 1, 1), as_of_utc.date(),
                    as_of_utc=as_of_utc)
            except TypeError:
                # backcompat: older bar_lookup callables without as_of_utc kwarg
                bars = self._bar_lookup(iid, date(1970, 1, 1), as_of_utc.date())
            result = self._inference.score(
                instrument_id=iid, as_of=as_of_utc,
                horizon_days=horizon_days, bars=bars,
            )
            if isinstance(result, Forecast):
                forecasts.append(result)
            else:
                no_forecasts.append(result)
        return forecasts, no_forecasts
