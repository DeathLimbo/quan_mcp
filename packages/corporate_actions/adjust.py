"""Split/dividend adjustment.

Forward-adjust: past prices scaled down by cumulative factor at ex-date.
Backward-adjust: current price series scaled up (or equivalently, past scaled
down and re-anchored). Backtests should use *back-adjust* to keep current price
unchanged; features that reference historical shares outstanding must use raw
prices + explicit adjustment lookups (spec §复权).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from enum import Enum

from packages.data_sources.contracts import Bar, CorporateAction


class AdjustMode(str, Enum):
    NONE = "none"
    FORWARD = "forward"    # past scaled to today's basis
    BACKWARD = "backward"  # today scaled to past basis


def _factor_for_action(a: CorporateAction, close_before_ex: Decimal) -> Decimal:
    """Multiplicative factor applied to prices BEFORE ex_date_local.

    Semantics per action type:
    - SPLIT   : ``ratio`` = new/old shares; past prices divided by ratio.
    - DIVIDEND: ``ratio`` = cash dividend per share; past prices multiplied
                by ``(close_before - dps) / close_before``.
    - SPINOFF : ``ratio`` = value per share of spun-off entity; treated like
                a special dividend of that value.
    - RIGHTS  : ``ratio`` = TERP / cum-rights close (pre-computed upstream).
                Applied as a direct multiplicative factor.
    - MERGER  : ``ratio`` = new-shares-per-old (stock-for-stock exchange).
                Past prices divided by ratio to keep the current share basis.
    Unknown types return an identity factor so a novel action never silently
    corrupts a series; ingestion must fail-closed instead.
    """
    r = a.ratio
    if a.action_type == "SPLIT":
        if r is None or r <= 0:
            return Decimal("1")
        return Decimal("1") / r
    if a.action_type == "DIVIDEND":
        d = r or Decimal("0")
        if close_before_ex <= 0:
            return Decimal("1")
        return (close_before_ex - d) / close_before_ex
    if a.action_type == "SPINOFF":
        # Treat spun-off value as an in-kind dividend.
        v = r or Decimal("0")
        if close_before_ex <= 0 or v >= close_before_ex:
            return Decimal("1")
        return (close_before_ex - v) / close_before_ex
    if a.action_type == "RIGHTS":
        # ``ratio`` already carries TERP / cum-rights close.
        if r is None or r <= 0:
            return Decimal("1")
        return r
    if a.action_type == "MERGER":
        # Stock-for-stock: new_per_old > 0 required.
        if r is None or r <= 0:
            return Decimal("1")
        return Decimal("1") / r
    return Decimal("1")


def apply_adjustment(
    bars: list[Bar],
    actions: list[CorporateAction],
    *,
    mode: AdjustMode = AdjustMode.BACKWARD,
) -> list[Bar]:
    if mode is AdjustMode.NONE or not actions:
        return list(bars)

    bars_sorted = sorted(bars, key=lambda b: b.market_local_date)
    # Map by date for lookup of close_before_ex
    by_date: dict[date, Bar] = {b.market_local_date: b for b in bars_sorted}

    # Compute per-action factor keyed by ex_date
    factors_at_ex: dict[date, Decimal] = {}
    for a in sorted(actions, key=lambda x: x.ex_date_local):
        # find last bar strictly before ex_date
        prev = None
        for b in bars_sorted:
            if b.market_local_date < a.ex_date_local:
                prev = b
            else:
                break
        close_before = prev.close if prev else Decimal("0")
        f = _factor_for_action(a, close_before)
        factors_at_ex[a.ex_date_local] = factors_at_ex.get(a.ex_date_local, Decimal("1")) * f

    if mode is AdjustMode.BACKWARD:
        # Prices on/after ex_date get MULTIPLIED by 1/f cumulatively going forward;
        # but the industry convention is: back-adjust divides past prices, keeping today intact.
        # Equivalent implementation: cumulate factors from oldest to newest and
        # apply reciprocal to bars BEFORE each ex_date.
        cum = Decimal("1")
        out: list[Bar] = []
        # Walk chronologically; when we cross an ex_date, cum *= f
        for b in bars_sorted:
            if b.market_local_date in factors_at_ex:
                cum *= factors_at_ex[b.market_local_date]
            # For backward-adjust, prices AT or AFTER ex_date are the anchor; scale earlier
            # bars by (product of future factors). Easiest: two-pass.
            out.append(b)
        # Two-pass: compute cumulative future factor for each date
        cum_future: dict[date, Decimal] = {}
        running = Decimal("1")
        for b in reversed(bars_sorted):
            cum_future[b.market_local_date] = running
            if b.market_local_date in factors_at_ex:
                running *= factors_at_ex[b.market_local_date]
        adjusted: list[Bar] = []
        for b in bars_sorted:
            f = cum_future[b.market_local_date]
            if f == 1:
                adjusted.append(b)
                continue
            adjusted.append(replace(
                b,
                open=(b.open * f).quantize(Decimal("0.0001")),
                high=(b.high * f).quantize(Decimal("0.0001")),
                low=(b.low * f).quantize(Decimal("0.0001")),
                close=(b.close * f).quantize(Decimal("0.0001")),
                adj_factor=(b.adj_factor or Decimal("1")) * f,
            ))
        return adjusted

    # FORWARD: today's prices scaled forward by cumulative past factors' reciprocal.
    # Symmetric to above.
    cum_past: dict[date, Decimal] = {}
    running = Decimal("1")
    for b in bars_sorted:
        if b.market_local_date in factors_at_ex:
            running /= factors_at_ex[b.market_local_date]
        cum_past[b.market_local_date] = running
    return [
        replace(
            b,
            open=(b.open * cum_past[b.market_local_date]).quantize(Decimal("0.0001")),
            high=(b.high * cum_past[b.market_local_date]).quantize(Decimal("0.0001")),
            low=(b.low * cum_past[b.market_local_date]).quantize(Decimal("0.0001")),
            close=(b.close * cum_past[b.market_local_date]).quantize(Decimal("0.0001")),
            adj_factor=(b.adj_factor or Decimal("1")) * cum_past[b.market_local_date],
        )
        for b in bars_sorted
    ]
