"""AKShare adapter — CN market (A-share equities, ETFs, funds, indices).

AKShare wraps a large collection of Chinese market data endpoints as pandas
DataFrames. We import lazily so this module is safe to load without the
runtime dependency present.

PIT contract mirrors the US adapter: CN sessions close 15:00 CST → 07:00 UTC.
An EOD lag (~1h) delays ``available_at_utc`` past close so downstream code
cannot read same-day close before it is officially published.

We map only a small subset of AKShare endpoints — the ones the rest of the
system actually needs. Anything not in this mapping raises ``NotImplementedError``
so we never silently return partial coverage.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Iterator, Literal

from packages.common.errors import DataConflictError, InternalError
from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import (
    Bar, FxRate, InstrumentDescriptor, MarketDataAdapter,
)


# 07:00 UTC == 15:00 Asia/Shanghai (CST is not observed DST).
_CN_SESSION_CLOSE_UTC = time(7, 0, tzinfo=timezone.utc)
_DEFAULT_EOD_LAG = timedelta(hours=1)


def _cn_session_close_utc(d: date) -> datetime:
    return datetime.combine(d, _CN_SESSION_CLOSE_UTC)


class AkshareAdapter(MarketDataAdapter):
    adapter_id = "akshare"
    supports_markets = frozenset({Market.CN})
    supports_asset_types = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.FUND, AssetType.INDEX})
    calendar_version = "cn.v0"
    rule_version = "cn.v0"
    # Provenance stamped onto every emitted Bar.
    source_version = "akshare.v1"
    license_tag = "PROVIDER_TOS"

    def __init__(self, *, eod_lag: timedelta = _DEFAULT_EOD_LAG) -> None:
        try:
            import akshare  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise InternalError(
                "akshare not installed; pip install 'cross-market-quant[data-sources]'"
            ) from e
        self._eod_lag = eod_lag

    def list_instruments(self, market: Market) -> Iterable[InstrumentDescriptor]:
        if market is not Market.CN:
            raise DataConflictError(f"akshare cannot serve {market}")
        import akshare as ak
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            venue = self._venue_for_code(code)
            iid = InstrumentId(market=Market.CN, venue=venue,
                               asset_type=AssetType.EQUITY, symbol=code)
            yield InstrumentDescriptor(
                instrument_id=iid,
                name_local=str(row.get("name", "")) or None,
                name_en=None, currency="CNY", lot_size=100,
                first_trade_date=None, last_trade_date=None, status="ACTIVE",
            )

    def fetch_bars_daily(
        self,
        instrument_id: InstrumentId,
        start: date,
        end: date,
        *,
        adjust: Literal["none", "forward", "backward"] = "none",
    ) -> Iterator[Bar]:
        if instrument_id.market is not Market.CN:
            raise DataConflictError(f"akshare cannot serve {instrument_id.market}")
        import akshare as ak
        # ak.stock_zh_a_hist takes YYYYMMDD strings and ``adjust`` uses codes
        # {"": no adjust, "qfq": forward, "hfq": backward}.
        code = instrument_id.symbol
        ak_adjust = {"none": "", "forward": "qfq", "backward": "hfq"}[adjust]
        if instrument_id.asset_type is AssetType.EQUITY:
            # Use the sina source (stock_zh_a_daily) — more stable than the
            # eastmoney push2his endpoint which is rate-limited / proxy-hostile.
            # sina needs a sh/sz prefix on the 6-digit code.
            sina_sym = self._sina_symbol(code, instrument_id.venue)
            df = ak.stock_zh_a_daily(symbol=sina_sym,
                                     start_date=start.strftime("%Y%m%d"),
                                     end_date=end.strftime("%Y%m%d"),
                                     adjust=ak_adjust)
        elif instrument_id.asset_type is AssetType.ETF:
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                     start_date=start.strftime("%Y%m%d"),
                                     end_date=end.strftime("%Y%m%d"),
                                     adjust=ak_adjust)
        elif instrument_id.asset_type is AssetType.FUND:
            # Open-end funds (incl. QDII) only publish NAV, not OHLCV.
            # Map NAV to a degenerate bar (O=H=L=C=NAV, volume=0).
            nav_df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            yield from self._fund_nav_to_bars(nav_df, instrument_id, start, end)
            return
        elif instrument_id.asset_type is AssetType.INDEX:
            # CN indices via akshare index daily.
            idx_symbol = code if code.startswith(("sh", "sz")) else f"sh{code}"
            df = ak.stock_zh_index_daily(symbol=idx_symbol)
        else:
            raise NotImplementedError(
                f"akshare daily bars for {instrument_id.asset_type} not wired yet"
            )
        if df is None or df.empty:
            return iter(())
        yield from self._df_to_bars(df, instrument_id)

    def fetch_fx_rates(
        self, *, base: str, quote: str, start: date, end: date,
    ) -> Iterator[FxRate]:
        """Historical FX via akshare Bank-of-China sina quotes (spec §3.2).

        ``currency_boc_sina`` returns BoC reference rates expressed as
        ``1 unit of foreign currency = N CNY``. We therefore natively fetch
        the foreign leg against CNY and invert when the caller asks for the
        reverse pair (e.g. CNY→USD). V1 wires USD/CNY only; any other pair
        raises ``NotImplementedError`` (fail-closed, no partial coverage).
        """
        import akshare as ak
        # Map ISO ccy → BoC sina symbol. Only USD is wired in V1.
        _BOC_SYMBOL = {"USD": "美元"}
        foreign = None
        invert = False
        if base == "CNY" and quote in _BOC_SYMBOL:
            foreign, invert = quote, True
        elif quote == "CNY" and base in _BOC_SYMBOL:
            foreign = base
        if foreign is None:
            raise NotImplementedError(
                f"akshare FX pair {base}/{quote} not wired (V1: USD/CNY only)"
            )
        df = ak.currency_boc_sina(symbol=_BOC_SYMBOL[foreign])
        if df is None or df.empty:
            return iter(())
        col_date = next((c for c in df.columns if c in ("日期", "date")), "日期")
        # BoC "中行折算价" is the mid reference rate (1 foreign = N CNY).
        col_rate = next(
            (c for c in df.columns if c in ("中行折算价", "rate")), "中行折算价"
        )
        for _, row in df.iterrows():
            d_raw = row[col_date]
            local_date = d_raw if isinstance(d_raw, date) else date.fromisoformat(str(d_raw)[:10])
            if local_date < start or local_date > end:
                continue
            boc_rate = Decimal(str(row[col_rate]))
            # BoC "中行折算价" is quoted as CNY per 100 units of foreign ccy.
            per_unit = boc_rate / Decimal("100")
            rate = (Decimal("1") / per_unit) if invert else per_unit
            event = _cn_session_close_utc(local_date)
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

    # ------ helpers -----------------------------------------------------

    @staticmethod
    def _venue_for_code(code: str) -> Venue:
        if code.startswith(("600", "601", "603", "605", "688", "689")):
            return Venue.SSE
        if code.startswith(("000", "001", "002", "003", "300", "301")):
            return Venue.SZSE
        if code.startswith(("43", "83", "87", "88")):
            return Venue.BSE
        # Default to SSE; caller may override via a curated alias table.
        return Venue.SSE

    @staticmethod
    def _sina_symbol(code: str, venue: Venue) -> str:
        """Prefix a 6-digit CN A-share code for the sina data source."""
        if venue is Venue.SZSE:
            return f"sz{code}"
        if venue is Venue.BSE:
            return f"bj{code}"
        return f"sh{code}"  # SSE default

    def _df_to_bars(self, df, iid: InstrumentId) -> Iterator[Bar]:
        # AKShare column names are Chinese; the canonical set is:
        # "日期"(date) "开盘"(open) "收盘"(close) "最高"(high) "最低"(low)
        # "成交量"(volume) "成交额"(turnover)
        col = {
            "date": next((c for c in df.columns if c in ("日期", "date")), "日期"),
            "open": next((c for c in df.columns if c in ("开盘", "open")), "开盘"),
            "close": next((c for c in df.columns if c in ("收盘", "close")), "收盘"),
            "high": next((c for c in df.columns if c in ("最高", "high")), "最高"),
            "low": next((c for c in df.columns if c in ("最低", "low")), "最低"),
            "vol": next((c for c in df.columns if c in ("成交量", "volume")), "成交量"),
            "amt": next((c for c in df.columns if c in ("成交额", "amount")), "成交额"),
        }
        for _, row in df.iterrows():
            d_raw = row[col["date"]]
            local_date = d_raw if isinstance(d_raw, date) else date.fromisoformat(str(d_raw)[:10])
            close = Decimal(str(row[col["close"]]))
            event = _cn_session_close_utc(local_date)
            yield Bar(
                instrument_id=iid,
                event_time_utc=event,
                market_local_date=local_date,
                open=Decimal(str(row[col["open"]])),
                high=Decimal(str(row[col["high"]])),
                low=Decimal(str(row[col["low"]])),
                close=close,
                volume=Decimal(str(row[col["vol"]])),
                turnover=Decimal(str(row[col["amt"]])) if col["amt"] in df.columns else None,
                adj_factor=Decimal("1"),   # akshare pre-adjusts; factor==1
                available_at_utc=event + self._eod_lag,
                source=self.adapter_id,
                calendar_version=self.calendar_version,
                rule_version=self.rule_version,
                source_version=self.source_version,
                license_tag=self.license_tag,
                quality_status="NORMAL",
            )

    def _fund_nav_to_bars(self, df, iid: InstrumentId,
                          start: date, end: date) -> Iterator[Bar]:
        """Map open-end fund NAV rows (单位净值) to degenerate Bars.

        Funds publish a single NAV per day — no OHLCV. We set
        O=H=L=C=NAV and volume=0 so the fund can flow through the same
        Bar-based feature / backtest / inference pipeline as equities.
        """
        if df is None or df.empty:
            return iter(())
        col_date = next((c for c in df.columns if c in ("净值日期", "date")), "净值日期")
        col_nav = next((c for c in df.columns if c in ("单位净值", "nav")), "单位净值")
        for _, row in df.iterrows():
            d_raw = row[col_date]
            local_date = d_raw if isinstance(d_raw, date) else date.fromisoformat(str(d_raw)[:10])
            if local_date < start or local_date > end:
                continue
            nav = Decimal(str(row[col_nav]))
            event = _cn_session_close_utc(local_date)
            yield Bar(
                instrument_id=iid,
                event_time_utc=event,
                market_local_date=local_date,
                open=nav, high=nav, low=nav, close=nav,
                volume=Decimal("0"),
                turnover=None,
                adj_factor=Decimal("1"),
                available_at_utc=event + self._eod_lag,
                source=self.adapter_id,
                calendar_version=self.calendar_version,
                rule_version=self.rule_version,
                source_version=self.source_version,
                license_tag=self.license_tag,
                quality_status="NORMAL",
            )
