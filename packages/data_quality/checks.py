"""Data-quality rule library (spec §75).

Seven layers, each with an explicit severity:

1. Schema        — required fields, types, nulls
2. Domain        — value ranges (positive prices, non-negative volume)
3. Cross-field   — internal invariants (high >= low, OHLC bounded)
4. Cross-source  — same instrument from two adapters should agree (± bp)
5. Temporal      — monotonic date, PIT ordering, calendar version stamped
6. Statistical   — return / volume spikes vs rolling window
7. Business      — halted / delisted / limit-hit consistency

Severity ladder — mapped to spec §75 outcomes:

- ``INFO``       — advisory only; recorded, does not block
- ``WARNING``    — usable for research but not for production trading
- ``ERROR``      — instrument blocked for the affected partition
- ``CRITICAL``   — entire ingestion partition blocked; ops must intervene

Callers are policy-owners: ingestion fails-closed on any ``ERROR`` or
``CRITICAL``; research pipelines may accept ``WARNING``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from statistics import median
from typing import Iterable, Sequence

from packages.common.instrument_id import InstrumentId
from packages.data_sources.contracts import Bar


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"           # legacy alias; kept so existing tests still pass
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# The four canonical severities per spec §75.
CANONICAL_SEVERITIES: tuple[Severity, ...] = (
    Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL,
)


class Layer(str, Enum):
    SCHEMA = "schema"
    DOMAIN = "domain"
    CROSS_FIELD = "cross_field"
    CROSS_SOURCE = "cross_source"
    TEMPORAL = "temporal"
    STATISTICAL = "statistical"
    BUSINESS = "business"


@dataclass(frozen=True, slots=True)
class DQFinding:
    rule: str
    severity: Severity
    instrument_id: InstrumentId
    reference: str        # e.g. isodate of the offending bar
    message: str
    layer: Layer = Layer.SCHEMA  # default so existing constructions still parse


# ---- Bar rules --------------------------------------------------------------

class BarChecks:
    """Run all bar-level DQ rules and return a flat list of findings."""

    def run(self, bars: Iterable[Bar]) -> list[DQFinding]:
        bars_list = list(bars)
        findings: list[DQFinding] = []
        prev_date = None
        for b in bars_list:
            findings.extend(self._check_one(b))
            if prev_date is not None and b.market_local_date <= prev_date:
                findings.append(DQFinding(
                    rule="monotonic_date",
                    severity=Severity.ERROR,
                    instrument_id=b.instrument_id,
                    reference=b.market_local_date.isoformat(),
                    message=f"date {b.market_local_date} not strictly greater than previous {prev_date}",
                    layer=Layer.TEMPORAL,
                ))
            prev_date = b.market_local_date
        findings.extend(self._statistical(bars_list))
        return findings

    def _check_one(self, b: Bar) -> list[DQFinding]:
        out: list[DQFinding] = []
        # Layer 3: cross-field
        if b.high < b.low:
            out.append(DQFinding(
                rule="high_ge_low", severity=Severity.ERROR,
                instrument_id=b.instrument_id, reference=b.market_local_date.isoformat(),
                message=f"high {b.high} < low {b.low}", layer=Layer.CROSS_FIELD,
            ))
        # Layer 2: domain
        for name, v in [("open", b.open), ("high", b.high), ("low", b.low), ("close", b.close)]:
            if v <= 0:
                out.append(DQFinding(
                    rule="positive_price", severity=Severity.ERROR,
                    instrument_id=b.instrument_id, reference=b.market_local_date.isoformat(),
                    message=f"{name} price non-positive: {v}", layer=Layer.DOMAIN,
                ))
        if b.volume < 0:
            out.append(DQFinding(
                rule="non_negative_volume", severity=Severity.ERROR,
                instrument_id=b.instrument_id, reference=b.market_local_date.isoformat(),
                message=f"volume negative: {b.volume}", layer=Layer.DOMAIN,
            ))
        # Layer 3: OHLC bound
        if not (b.low <= b.open <= b.high) or not (b.low <= b.close <= b.high):
            out.append(DQFinding(
                rule="ohlc_bounded", severity=Severity.ERROR,
                instrument_id=b.instrument_id, reference=b.market_local_date.isoformat(),
                message="open/close outside [low, high]", layer=Layer.CROSS_FIELD,
            ))
        # Layer 5: temporal
        if b.available_at_utc < b.event_time_utc:
            out.append(DQFinding(
                rule="available_after_event", severity=Severity.CRITICAL,
                instrument_id=b.instrument_id, reference=b.market_local_date.isoformat(),
                message="available_at_utc is before event_time_utc (violates PIT)",
                layer=Layer.TEMPORAL,
            ))
        # Layer 1: schema
        if not b.calendar_version or not b.rule_version:
            out.append(DQFinding(
                rule="versions_stamped", severity=Severity.ERROR,
                instrument_id=b.instrument_id, reference=b.market_local_date.isoformat(),
                message="missing calendar_version or rule_version", layer=Layer.SCHEMA,
            ))
        return out

    # Layer 6: statistical — outlier daily returns.
    def _statistical(self, bars: Sequence[Bar]) -> list[DQFinding]:
        if len(bars) < 20:
            return []
        # Simple day-over-day return z-score against median absolute deviation.
        rets: list[float] = []
        for prev, cur in zip(bars[:-1], bars[1:]):
            if prev.close <= 0:
                rets.append(0.0)
            else:
                rets.append(float((cur.close - prev.close) / prev.close))
        if not rets:
            return []
        med = median(rets)
        mad = median(abs(r - med) for r in rets) or 1e-9
        out: list[DQFinding] = []
        for i, r in enumerate(rets):
            z = abs(r - med) / mad
            if z > 15:  # very extreme
                b = bars[i + 1]
                out.append(DQFinding(
                    rule="return_outlier", severity=Severity.WARNING,
                    instrument_id=b.instrument_id,
                    reference=b.market_local_date.isoformat(),
                    message=f"|r|={abs(r):.4f}, z={z:.1f}, median={med:.4f}, MAD={mad:.4f}",
                    layer=Layer.STATISTICAL,
                ))
        return out


# ---- Cross-source rules (Layer 4) -------------------------------------------

def cross_source_bar_check(
    primary: Sequence[Bar], secondary: Sequence[Bar],
    *, bps_tolerance: int = 20,
) -> list[DQFinding]:
    """Compare two adapters' close prices on the same instrument+date.

    A tolerance of 20bps (0.20%) allows for tick-different snapshots but
    catches ticker-swap / adjustment mistakes.
    """
    idx = {(b.instrument_id.canonical(), b.market_local_date): b for b in secondary}
    out: list[DQFinding] = []
    for a in primary:
        key = (a.instrument_id.canonical(), a.market_local_date)
        b = idx.get(key)
        if b is None:
            continue
        if a.close == 0:
            continue
        diff_bps = abs(float((a.close - b.close) / a.close)) * 10_000
        if diff_bps > bps_tolerance:
            out.append(DQFinding(
                rule="close_agreement",
                severity=Severity.WARNING if diff_bps < 100 else Severity.ERROR,
                instrument_id=a.instrument_id,
                reference=a.market_local_date.isoformat(),
                message=f"close disagrees by {diff_bps:.1f}bps ({a.close} vs {b.close})",
                layer=Layer.CROSS_SOURCE,
            ))
    return out


# ---- Business rules (Layer 7) -----------------------------------------------

def business_state_check(
    bar: Bar, *,
    is_halted: bool = False,
    is_delisted: bool = False,
) -> list[DQFinding]:
    """A halted / delisted instrument must not have positive volume."""
    out: list[DQFinding] = []
    if (is_halted or is_delisted) and bar.volume > 0:
        out.append(DQFinding(
            rule="halted_no_volume",
            severity=Severity.CRITICAL,
            instrument_id=bar.instrument_id,
            reference=bar.market_local_date.isoformat(),
            message=f"instrument marked halted/delisted but volume={bar.volume}",
            layer=Layer.BUSINESS,
        ))
    return out


# ---- Roll-ups --------------------------------------------------------------

def has_errors(findings: Iterable[DQFinding]) -> bool:
    """Any ERROR/CRITICAL finding blocks ingestion (fail-closed)."""
    return any(f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings)


def has_critical(findings: Iterable[DQFinding]) -> bool:
    return any(f.severity is Severity.CRITICAL for f in findings)


def by_severity(findings: Iterable[DQFinding]) -> dict[Severity, list[DQFinding]]:
    out: dict[Severity, list[DQFinding]] = {s: [] for s in CANONICAL_SEVERITIES}
    for f in findings:
        # Fold legacy WARN into WARNING for counting.
        key = Severity.WARNING if f.severity is Severity.WARN else f.severity
        if key in out:
            out[key].append(f)
    return out
