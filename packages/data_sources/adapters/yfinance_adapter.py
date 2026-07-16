"""yfinance adapter — US market (equities/ETFs/indices).

yfinance is an unofficial Yahoo Finance client that returns pandas DataFrames.
We import lazily so the module loads cleanly on hosts that only run unit
tests without the heavy dependency chain.

Point-in-time contract:
- ``event_time_utc``   = session close in UTC (approx 20:00 UTC for
  NASDAQ/NYSE regular sessions; adapter delegates to ``session_close_utc``).
- ``available_at_utc`` = session close + configurable EOD publish delay so
  callers cannot read a bar strictly before the market publishes it.
- ``adj_factor`` derived from Yahoo's `Adj Close` / `Close` ratio; kept as
  cumulative so retro-adjustments never rewrite history.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Iterator, Literal

from packages.common.errors import DataConflictError, InternalError
from packages.common.instrument_id import AssetType, InstrumentId, Market
from packages.data_sources.contracts import (
    Bar, FxRate, InstrumentDescriptor, MarketDataAdapter,
)


# 20:00 UTC ≈ 16:00 America/New_York during EDT; DST-safe enough for daily bars.
_US_SESSION_CLOSE_UTC = time(20, 0, tzinfo=timezone.utc)
_DEFAULT_EOD_LAG = timedelta(minutes=30)


def _session_close_utc(d: date) -> datetime:
    return datetime.combine(d, _US_SESSION_CLOSE_UTC)


class YFinanceAdapter(MarketDataAdapter):
    adapter_id = "yfinance"
    supports_markets = frozenset({Market.US})
    supports_asset_types = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
    calendar_version = "us.v0"
    rule_version = "us.v0"
    # Provenance stamped onto every emitted Bar.
    source_version = "yfinance.v1"
    license_tag = "PROVIDER_TOS"

    def __init__(self, *, eod_lag: timedelta = _DEFAULT_EOD_LAG) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise InternalError(
                "yfinance not installed; pip install 'cross-market-quant[data-sources]'"
            ) from e
        self._eod_lag = eod_lag

    def list_instruments(self, market: Market) -> Iterable[InstrumentDescriptor]:
        # yfinance does not publish a full universe. Callers must supply a
        # symbol list (e.g. S&P 500 constituents from a curated source). We
        # refuse to fabricate a universe to avoid silent gaps.
        raise NotImplementedError(
            "yfinance has no bulk universe listing; provide symbols from an external constituent source"
        )

    def fetch_bars_daily(
        self,
        instrument_id: InstrumentId,
        start: date,
        end: date,
        *,
        adjust: Literal["none", "forward", "backward"] = "none",
    ) -> Iterator[Bar]:
        if instrument_id.market is not Market.US:
            raise DataConflictError(f"yfinance cannot serve {instrument_id.market}")
        import yfinance as yf  # local import to keep module cheap
        # yfinance treats `end` as exclusive; extend by 1 day to match our inclusive contract.
        df = yf.Ticker(instrument_id.symbol).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False, actions=False, raise_errors=False,
        )
        if df is None or df.empty:
            return iter(())
        yield from self._df_to_bars(df, instrument_id, adjust=adjust)

    def fetch_fx_rates(
        self, *, base: str, quote: str, start: date, end: date,
    ) -> Iterator[FxRate]:
        """Historical FX via yfinance (spec §3.2 FX Adapter).

        yfinance quotes any pair as ``{BASE}{QUOTE}=X`` returning daily Close
        expressed as ``1 base = N quote`` (e.g. USDCNY=X → 1 USD = 7.18 CNY).
        We treat the FX tape as a 24h market but stamp a daily close at the
        US session close UTC for consistency with the equity PIT contract.
        """
        import yfinance as yf
        symbol = f"{base}{quote}=X"
        df = yf.Ticker(symbol).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False, actions=False, raise_errors=False,
        )
        if df is None or df.empty:
            return iter(())
        for ts, row in df.iterrows():
            local_date = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            rate = Decimal(str(row["Close"]))
            event = _session_close_utc(local_date)
            yield FxRate(
                base_ccy=base, quote_ccy=quote,
                market_local_date=local_date,
                rate=rate,
                event_time_utc=event,
                available_at_utc=event + self._eod_lag,
                source=self.adapter_id,
                source_version=self.source_version,
                license_tag=self.license_tag,
                quality_status="NORMAL",
            )

    # -- transforms (kept public-static-ish for unit-testable seams) ------
    def _df_to_bars(
        self,
        df,
        iid: InstrumentId,
        *,
        adjust: Literal["none", "forward", "backward"],
    ) -> Iterator[Bar]:
        for ts, row in df.iterrows():
            local_date = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            close = Decimal(str(row["Close"]))
            adj_close_raw = row.get("Adj Close")
            adj_close = Decimal(str(adj_close_raw)) if adj_close_raw is not None else close
            adj_factor = (adj_close / close) if close != 0 else Decimal("1")
            open_, high, low = (Decimal(str(row[k])) for k in ("Open", "High", "Low"))
            if adjust == "forward":
                open_, high, low, close = (x * adj_factor for x in (open_, high, low, close))
            elif adjust == "backward":
                # Legacy back-adjust: return raw prices; factor tells caller how to scale.
                pass
            event = _session_close_utc(local_date)
            yield Bar(
                instrument_id=iid,
                event_time_utc=event,
                market_local_date=local_date,
                open=open_, high=high, low=low, close=close,
                volume=Decimal(str(row.get("Volume", 0) or 0)),
                turnover=None,
                adj_factor=adj_factor,
                available_at_utc=event + self._eod_lag,
                source=self.adapter_id,
                calendar_version=self.calendar_version,
                rule_version=self.rule_version,
                source_version=self.source_version,
                license_tag=self.license_tag,
                quality_status="NORMAL",
            )
