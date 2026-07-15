"""Data source adapter contracts.

The system MUST talk to any raw provider (AKShare, Tushare, yfinance, SEC EDGAR,
FRED, exchange feed) only through these Protocols. Swapping providers is a
config change, never a code change in downstream modules.
"""
from packages.data_sources.contracts import (
    Bar, CorporateAction, FundNAV, InstrumentDescriptor,
    MarketDataAdapter, FundamentalAdapter, CorporateActionAdapter,
    RateLimitError, ProviderError,
)
from packages.data_sources.registry import (
    register_adapter, get_adapter, list_adapters,
)

__all__ = [
    "Bar", "CorporateAction", "FundNAV", "InstrumentDescriptor",
    "MarketDataAdapter", "FundamentalAdapter", "CorporateActionAdapter",
    "RateLimitError", "ProviderError",
    "register_adapter", "get_adapter", "list_adapters",
]
