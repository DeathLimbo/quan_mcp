"""Fundamentals-based features — spec §12.2 横截面/基本面因子.

These features declare ``requires_fundamentals=True`` and consume a
:class:`FundamentalContext` carrying PIT-safe raw facts (EPS, book value,
shares outstanding, etc. — see :class:`packages.fundamentals.facts.FactName`)
plus the latest bar's close to derive valuation / quality / leverage ratios.

All are single-date (``lookback_days=1``): they read ``bars[-1].close`` and
the as-of fundamentals. Cross-sectional relatives (industry-median PE) need a
sector peer set and are deferred — the infrastructure (``FundamentalContext.
sector``) is in place for a future extension.

Fact keys mirror ``FactName.value`` strings so a ``fund_ctx_provider`` built
from ``FactStore`` maps 1:1.
"""
from __future__ import annotations

import math
from typing import Sequence

from packages.data_sources.contracts import Bar
from packages.features.registry import FundamentalContext, feature


def _close(bars: Sequence[Bar]) -> float | None:
    return float(bars[-1].close) if bars else None


def _fact(ctx: FundamentalContext, key: str) -> float | None:
    v = ctx.facts.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- valuation ------------------------------------------------------------

@feature("pe_ratio", lookback_days=1, requires_fundamentals=True)
def pe_ratio(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Price-to-earnings = close / EPS. None if EPS missing or <= 0."""
    close = _close(bars)
    eps = _fact(ctx, "eps")
    if close is None or eps is None or eps <= 0:
        return None
    return close / eps


@feature("pb_ratio", lookback_days=1, requires_fundamentals=True)
def pb_ratio(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Price-to-book = close / book_value_per_share."""
    close = _close(bars)
    bvps = _fact(ctx, "book_value_per_share")
    if close is None or bvps is None or bvps <= 0:
        return None
    return close / bvps


@feature("earnings_yield", lookback_days=1, requires_fundamentals=True)
def earnings_yield(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """E/P = EPS / close. The value factor counterpart to PE."""
    close = _close(bars)
    eps = _fact(ctx, "eps")
    if close is None or close == 0 or eps is None:
        return None
    return eps / close


@feature("dividend_yield", lookback_days=1, requires_fundamentals=True)
def dividend_yield(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Dividend per share / close."""
    close = _close(bars)
    dps = _fact(ctx, "dividend_per_share")
    if close is None or close == 0 or dps is None:
        return None
    return dps / close


@feature("log_market_cap", lookback_days=1, requires_fundamentals=True)
def log_market_cap(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """ln(close * shares_outstanding). Log-scale size factor."""
    close = _close(bars)
    shares = _fact(ctx, "shares_outstanding")
    if close is None or shares is None or shares <= 0:
        return None
    mc = close * shares
    if mc <= 0:
        return None
    return math.log(mc)


# ---- quality / profitability ---------------------------------------------

@feature("roe", lookback_days=1, requires_fundamentals=True)
def roe(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Return on equity = net_income / (book_value_per_share * shares)."""
    ni = _fact(ctx, "net_income")
    bvps = _fact(ctx, "book_value_per_share")
    shares = _fact(ctx, "shares_outstanding")
    if ni is None or bvps is None or shares is None or shares <= 0:
        return None
    equity = bvps * shares
    if equity == 0:
        return None
    return ni / equity


@feature("cashflow_yield", lookback_days=1, requires_fundamentals=True)
def cashflow_yield(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Operating cashflow per share / close. Cash-earnings yield."""
    close = _close(bars)
    ocf = _fact(ctx, "operating_cashflow")
    shares = _fact(ctx, "shares_outstanding")
    if close is None or close == 0 or ocf is None or shares is None or shares <= 0:
        return None
    return (ocf / shares) / close


# ---- leverage / liquidity -------------------------------------------------

@feature("debt_to_equity", lookback_days=1, requires_fundamentals=True)
def debt_to_equity(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Total debt / book equity."""
    debt = _fact(ctx, "total_debt")
    bvps = _fact(ctx, "book_value_per_share")
    shares = _fact(ctx, "shares_outstanding")
    if debt is None or bvps is None or shares is None:
        return None
    equity = bvps * shares
    if equity == 0:
        return None
    return debt / equity


@feature("cash_to_debt", lookback_days=1, requires_fundamentals=True)
def cash_to_debt(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Cash & equivalents / total debt. Liquidity buffer (>1 = net cash)."""
    cash = _fact(ctx, "cash_and_equiv")
    debt = _fact(ctx, "total_debt")
    if cash is None or debt is None or debt == 0:
        return None
    return cash / debt


# ---- fund-specific --------------------------------------------------------

@feature("fund_expense_ratio", lookback_days=1, requires_fundamentals=True)
def fund_expense_ratio(bars: Sequence[Bar], ctx: FundamentalContext) -> float | None:
    """Open-end fund / ETF annual expense ratio (cost drag)."""
    return _fact(ctx, "fund_expense_ratio")
