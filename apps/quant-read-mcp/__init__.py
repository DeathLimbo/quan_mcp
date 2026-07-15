"""quant-read-mcp: read-only MCP server.

Exposes safe, PIT-clean read tools for research agents:
- get_instrument(instrument_id) -> instrument metadata
- get_bars(instrument_id, from, to) -> historical bars
- score(instrument_id, as_of, horizon) -> Forecast | NoForecast
- backtest_report(strategy_id) -> markdown report
- risk_check(intent) -> risk trace (dry-run, no side effect)

Design:
- No write operations. No state changes. No trading.
- All results include ``as_of`` and ``feature_hash`` for reproducibility.
- Errors are structured (ApiResponse). Missing data -> NO_FORECAST, never a
  silent zero or interpolated value.
- The transport layer (FastAPI or stdio) is intentionally minimal — the value
  is in the *typed* boundary of the tools list.
"""
