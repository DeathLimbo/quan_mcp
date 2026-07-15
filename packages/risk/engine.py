"""Risk engine core.

Layer order (spec §风险引擎 8 层, exact sequence):
  1. permission          — user/role/market entitlement
  2. regulatory          — hard rules (no short in CN A-share for retail, wash sale, etc.)
  3. market_state        — halted / delisted / suspended / calendar closed
  4. price_limit_and_liquidity — CN daily limit; US LULD/circuit; volume floor
  5. exposure_limits     — per-name / per-sector / gross / net
  6. stress_delta        — scenario shock ceiling
  7. operational         — kill-switch, throttle, dedup
  8. execution_feasibility — venue open, min-lot, tick size

Each layer returns a ``RiskDecision`` with ``verdict``, ``code`` and
human-readable ``reason``. First REJECT short-circuits; REVIEW is surfaced
alongside the running trace so an operator can decide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Sequence

from packages.common.instrument_id import InstrumentId, Market
from packages.calendar_rule.rules import PriceLimitRule, get_price_limit


class RiskVerdict(str, Enum):
    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    layer: str
    verdict: RiskVerdict
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class RiskContext:
    instrument_id: InstrumentId
    trade_date: date
    side: int                        # +1 buy, -1 sell
    quantity: float
    ref_price: float                 # decision-time reference (last close)
    # Environmental / policy
    user_permissions: frozenset[str] = frozenset()
    allow_short: bool = False
    market_open: bool = True
    instrument_halted: bool = False
    instrument_delisted: bool = False
    is_st: bool = False              # for CN limit rule choice
    board: str = "MAIN"              # MAIN, STAR, CHINEXT, BSE (CN)
    prev_close: float | None = None  # for CN price-limit math
    avg_volume_20d: float | None = None
    exposure_frac_current: float = 0.0
    exposure_frac_limit: float = 0.20
    gross_frac_current: float = 0.0
    gross_frac_limit: float = 1.0
    stress_shock_bps: float = 0.0
    stress_shock_limit_bps: float = 3000.0
    kill_switch: bool = False
    duplicate_intent: bool = False
    min_lot: int = 1
    tick_size: float = 0.01


RiskLayer = Callable[[RiskContext], RiskDecision]


# ---- layers -----------------------------------------------------------------

def _l1_permission(ctx: RiskContext) -> RiskDecision:
    needed = f"trade:{ctx.instrument_id.market.value}"
    if needed not in ctx.user_permissions:
        return RiskDecision("permission", RiskVerdict.REJECT, "PERM_DENIED",
                            f"missing permission {needed}")
    return RiskDecision("permission", RiskVerdict.ACCEPT, "OK", "permitted")


def _l2_regulatory(ctx: RiskContext) -> RiskDecision:
    if ctx.side < 0 and ctx.instrument_id.market is Market.CN and not ctx.allow_short:
        return RiskDecision("regulatory", RiskVerdict.REJECT, "NO_SHORT_CN",
                            "short-selling CN cash equities not permitted for this account")
    return RiskDecision("regulatory", RiskVerdict.ACCEPT, "OK", "regulatory ok")


def _l3_market_state(ctx: RiskContext) -> RiskDecision:
    if ctx.instrument_delisted:
        return RiskDecision("market_state", RiskVerdict.REJECT, "DELISTED", "instrument delisted")
    if ctx.instrument_halted:
        return RiskDecision("market_state", RiskVerdict.REJECT, "HALTED", "instrument halted")
    if not ctx.market_open:
        return RiskDecision("market_state", RiskVerdict.REJECT, "MARKET_CLOSED",
                            "market closed on trade_date")
    return RiskDecision("market_state", RiskVerdict.ACCEPT, "OK", "market state ok")


def _l4_price_limit_and_liquidity(ctx: RiskContext) -> RiskDecision:
    # Price limit (CN only in v1)
    if ctx.instrument_id.market is Market.CN and ctx.prev_close is not None:
        name = f"ST {ctx.instrument_id.symbol}" if ctx.is_st else None
        rule: PriceLimitRule = get_price_limit(ctx.instrument_id, name_local=name)
        if rule.up_pct is not None and rule.down_pct is not None:
            upper = ctx.prev_close * (1 + float(rule.up_pct))
            lower = ctx.prev_close * (1 + float(rule.down_pct))
            if ctx.side > 0 and ctx.ref_price >= upper - 1e-9:
                return RiskDecision("price_limit_and_liquidity", RiskVerdict.REJECT,
                                    "AT_UPPER_LIMIT", f"price {ctx.ref_price} at upper {upper}")
            if ctx.side < 0 and ctx.ref_price <= lower + 1e-9:
                return RiskDecision("price_limit_and_liquidity", RiskVerdict.REJECT,
                                    "AT_LOWER_LIMIT", f"price {ctx.ref_price} at lower {lower}")
    # Liquidity floor
    if ctx.avg_volume_20d is not None and ctx.quantity > 0.10 * ctx.avg_volume_20d:
        return RiskDecision("price_limit_and_liquidity", RiskVerdict.REVIEW,
                            "LIQUIDITY_THIN",
                            f"qty {ctx.quantity} > 10% of adv20 {ctx.avg_volume_20d}")
    return RiskDecision("price_limit_and_liquidity", RiskVerdict.ACCEPT, "OK", "price/liq ok")


def _l5_exposure_limits(ctx: RiskContext) -> RiskDecision:
    """Check post-trade fractions against caller-supplied limits.

    The caller is responsible for computing ``exposure_frac_current`` and
    ``gross_frac_current`` as *post-trade* fractions of equity — this keeps
    the engine free of position-management concerns and portable across
    account types.
    """
    if ctx.exposure_frac_current > ctx.exposure_frac_limit + 1e-12:
        return RiskDecision("exposure_limits", RiskVerdict.REJECT, "NAME_LIMIT",
                            f"per-name exposure {ctx.exposure_frac_current} > limit {ctx.exposure_frac_limit}")
    if ctx.gross_frac_current > ctx.gross_frac_limit + 1e-12:
        return RiskDecision("exposure_limits", RiskVerdict.REJECT, "GROSS_LIMIT",
                            f"gross exposure {ctx.gross_frac_current} > limit {ctx.gross_frac_limit}")
    return RiskDecision("exposure_limits", RiskVerdict.ACCEPT, "OK", "exposure ok")


def _l6_stress_delta(ctx: RiskContext) -> RiskDecision:
    if ctx.stress_shock_bps > ctx.stress_shock_limit_bps:
        return RiskDecision("stress_delta", RiskVerdict.REJECT, "STRESS_LIMIT",
                            f"scenario shock {ctx.stress_shock_bps}bps > {ctx.stress_shock_limit_bps}bps")
    return RiskDecision("stress_delta", RiskVerdict.ACCEPT, "OK", "stress ok")


def _l7_operational(ctx: RiskContext) -> RiskDecision:
    if ctx.kill_switch:
        return RiskDecision("operational", RiskVerdict.REJECT, "KILL_SWITCH", "kill-switch engaged")
    if ctx.duplicate_intent:
        return RiskDecision("operational", RiskVerdict.REJECT, "DUP_INTENT", "duplicate intent detected")
    return RiskDecision("operational", RiskVerdict.ACCEPT, "OK", "operational ok")


def _l8_execution_feasibility(ctx: RiskContext) -> RiskDecision:
    if ctx.min_lot > 0 and int(ctx.quantity) % ctx.min_lot != 0:
        return RiskDecision("execution_feasibility", RiskVerdict.REJECT, "MIN_LOT",
                            f"qty {ctx.quantity} not multiple of min_lot {ctx.min_lot}")
    # tick check: ref_price should round to tick
    if ctx.tick_size > 0:
        ticks = round(ctx.ref_price / ctx.tick_size)
        if abs(ticks * ctx.tick_size - ctx.ref_price) > 1e-6:
            return RiskDecision("execution_feasibility", RiskVerdict.REVIEW, "TICK_ROUND",
                                f"price {ctx.ref_price} not on tick {ctx.tick_size}")
    return RiskDecision("execution_feasibility", RiskVerdict.ACCEPT, "OK", "execution feasible")


DEFAULT_LAYERS: tuple[RiskLayer, ...] = (
    _l1_permission, _l2_regulatory, _l3_market_state,
    _l4_price_limit_and_liquidity, _l5_exposure_limits,
    _l6_stress_delta, _l7_operational, _l8_execution_feasibility,
)


@dataclass(frozen=True, slots=True)
class RiskEngine:
    layers: tuple[RiskLayer, ...] = DEFAULT_LAYERS

    def evaluate(self, ctx: RiskContext) -> list[RiskDecision]:
        trace: list[RiskDecision] = []
        for layer in self.layers:
            d = layer(ctx)
            trace.append(d)
            if d.verdict is RiskVerdict.REJECT:
                break
        return trace

    def final_verdict(self, trace: Sequence[RiskDecision]) -> RiskVerdict:
        if any(d.verdict is RiskVerdict.REJECT for d in trace):
            return RiskVerdict.REJECT
        if any(d.verdict is RiskVerdict.REVIEW for d in trace):
            return RiskVerdict.REVIEW
        return RiskVerdict.ACCEPT


def default_engine() -> RiskEngine:
    return RiskEngine()
