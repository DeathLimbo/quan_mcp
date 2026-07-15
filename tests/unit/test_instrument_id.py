import pytest

from packages.common import (
    AssetType,
    InstrumentId,
    Market,
    Venue,
    parse_instrument_id,
)


def test_canonical_roundtrip():
    iid = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")
    assert iid.canonical() == "CN.SSE.EQUITY.600519"
    assert parse_instrument_id(iid.canonical()) == iid


def test_us_equity_multi_venue():
    a = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "aapl")  # normalized upper
    b = InstrumentId(Market.US, Venue.NYSE, AssetType.EQUITY, "AAPL")
    assert a.symbol == "AAPL"
    assert a != b, "same ticker on different venues must NOT collide"


def test_venue_asset_type_mismatch_rejected():
    # CFFEX is index/futures venue, not equity
    with pytest.raises(ValueError):
        InstrumentId(Market.CN, Venue.CFFEX, AssetType.EQUITY, "IF2409")
    # NASDAQ cannot host CN A-share
    with pytest.raises(ValueError):
        InstrumentId(Market.CN, Venue.NASDAQ, AssetType.EQUITY, "600519")


def test_empty_symbol_rejected():
    with pytest.raises(ValueError):
        InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "  ")


def test_fund_venue_bound():
    f = InstrumentId(Market.CN, Venue.CN_FUND, AssetType.FUND, "110022")
    assert f.canonical() == "CN.CN_FUND.FUND.110022"
    with pytest.raises(ValueError):
        InstrumentId(Market.CN, Venue.SSE, AssetType.FUND, "110022")


def test_parse_invalid_form():
    with pytest.raises(ValueError):
        parse_instrument_id("AAPL")
    with pytest.raises(ValueError):
        parse_instrument_id("US.NASDAQ.EQUITY")
