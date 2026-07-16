"""Phase 5: Shadow tracking + drift auto-suspend (issue #10 §11).

ShadowTracker is the *forward-tracking* layer — the answer to "how do you
verify a future prediction without assuming history == future". Predictions
are recorded white-on-black; when their horizon elapses, they are settled
against the realised return. The resulting live IC / hit-rate / drawdown is
the only honest measure of "does this strategy still work *now*".

DriftAutoSuspender watches that live track record: if recent live IC collapses
or drawdown blows through a limit, the strategy is auto-suspended — no human
needs to be watching. This is the fail-closed safety net for live strategies.

Storage is in-memory by default (test-friendly, no DB coupling); a DB-backed
tracker can wrap the same interface later.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from packages.evaluation.metrics import information_coefficient


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ShadowPrediction:
    prediction_id: str
    strategy_id: str
    version: str
    instrument_id: str
    as_of: date              # decision time (PIT)
    horizon_days: int
    expected_return: float   # predicted horizon return
    model_ref: str | None = None


@dataclass
class ShadowOutcome:
    prediction: ShadowPrediction
    actual_return: float | None = None   # None until settled
    settled_at: date | None = None

    @property
    def is_settled(self) -> bool:
        return self.actual_return is not None


# Callable: given (instrument_id, as_of, settle_date) -> realised return | None
ActualReturnFn = Callable[[str, date, date], float | None]


# --------------------------------------------------------------------------- #
# ShadowTracker
# --------------------------------------------------------------------------- #
class ShadowTracker:
    """Forward-tracking record of a strategy's live predictions.

    The flow: record_prediction (each decision day) -> settle_due (after
    horizon elapses, fetch realised returns) -> live_track_record (read the
    honest, out-of-sample accuracy). Nothing here touches historical training
    data — it only records what was predicted and what actually happened.
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, ShadowOutcome] = {}

    def record_prediction(
        self,
        *,
        strategy_id: str,
        version: str,
        instrument_id: str,
        as_of: date,
        horizon_days: int,
        expected_return: float,
        model_ref: str | None = None,
    ) -> ShadowPrediction:
        pred = ShadowPrediction(
            prediction_id=f"sp_{uuid.uuid4().hex[:12]}",
            strategy_id=strategy_id, version=version,
            instrument_id=instrument_id, as_of=as_of,
            horizon_days=horizon_days, expected_return=expected_return,
            model_ref=model_ref,
        )
        self._outcomes[pred.prediction_id] = ShadowOutcome(prediction=pred)
        return pred

    def settle_due(
        self,
        *,
        as_of: date,
        actual_return_fn: ActualReturnFn,
    ) -> list[ShadowOutcome]:
        """Settle predictions whose horizon has elapsed by ``as_of``.

        For each unsettled prediction, compute the settle date = as_of +
        horizon_days (trading-day approximation). If ``actual_return_fn``
        returns a value, record it; if None, the data isn't ready yet.
        """
        settled: list[ShadowOutcome] = []
        for outcome in self._outcomes.values():
            if outcome.is_settled:
                continue
            p = outcome.prediction
            settle_date = p.as_of + _days(p.horizon_days)
            if settle_date > as_of:
                continue  # horizon not yet elapsed
            actual = actual_return_fn(p.instrument_id, p.as_of, settle_date)
            if actual is not None:
                outcome.actual_return = float(actual)
                outcome.settled_at = as_of
                settled.append(outcome)
        return settled

    def live_track_record(
        self,
        *,
        strategy_id: str,
        recent_n: int = 50,
    ) -> dict[str, float]:
        """Honest out-of-sample accuracy of recent settled predictions.

        Returns ic / hit_rate / rmse / max_drawdown / n_settled. Empty if no
        settled predictions yet (caller should treat n_settled < min_sample as
        'insufficient evidence')."""
        settled = [
            o for o in self._outcomes.values()
            if o.is_settled and o.prediction.strategy_id == strategy_id
        ]
        settled.sort(key=lambda o: o.prediction.as_of, reverse=True)
        settled = settled[:recent_n]
        if not settled:
            return {"ic": 0.0, "hit_rate": 0.0, "rmse": 0.0,
                    "max_drawdown": 0.0, "n_settled": 0.0}
        preds = [o.prediction.expected_return for o in settled]
        actuals = [o.actual_return for o in settled]  # type: ignore[list-item]
        ic = information_coefficient(preds, actuals) if len(preds) > 1 else 0.0
        n = len(preds)
        hits = sum(1 for p, a in zip(preds, actuals) if (p > 0) == (a > 0))
        rmse = math.sqrt(sum((p - a) ** 2 for p, a in zip(preds, actuals)) / n)
        # drawdown over the realised-return sequence (strategy long-biased)
        equity = [1.0]
        for a in actuals:
            equity.append(equity[-1] * (1.0 + a))
        mdd = _max_drawdown(equity)
        return {
            "ic": ic, "hit_rate": hits / n, "rmse": rmse,
            "max_drawdown": mdd, "n_settled": float(n),
        }

    def predictions_for(self, strategy_id: str) -> list[ShadowOutcome]:
        return [o for o in self._outcomes.values()
                if o.prediction.strategy_id == strategy_id]


def _days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


def _max_drawdown(equity: list[float]) -> float:
    if len(equity) < 2:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd
    return mdd


# --------------------------------------------------------------------------- #
# DriftAutoSuspender
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DriftAutoSuspendConfig:
    min_live_ic: float = 0.02      # below this, the strategy lost its edge
    max_drawdown: float = -0.15    # deeper than this, suspend
    min_sample: int = 10           # need this many settled preds before acting
    recent_n: int = 50             # lookback window for the track record


class DriftAutoSuspender:
    """Watches a ShadowTracker's live record and auto-suspends on drift.

    This is the fail-closed safety net: if a strategy in SHADOW/CANARY sees its
    live IC collapse or drawdown blow through a limit, it is suspended without
    waiting for a human. The bar is deliberately conservative — a strategy must
    prove it STILL works, not just that it once did.
    """

    def __init__(self, cfg: DriftAutoSuspendConfig | None = None) -> None:
        self._cfg = cfg or DriftAutoSuspendConfig()

    def should_suspend(self, track: dict[str, float]) -> tuple[bool, str]:
        """Pure decision over a live_track_record dict.

        Returns (suspend, reason). No I/O — the service layer performs the
        actual transition."""
        n = int(track.get("n_settled", 0))
        if n < self._cfg.min_sample:
            return False, f"insufficient sample ({n} < {self._cfg.min_sample})"
        ic = track.get("ic", 0.0)
        mdd = track.get("max_drawdown", 0.0)
        if ic < self._cfg.min_live_ic:
            return True, (f"live IC {ic:+.4f} < {self._cfg.min_live_ic} "
                          f"(edge collapsed over {n} settled preds)")
        if mdd < self._cfg.max_drawdown:
            return True, (f"live max_drawdown {mdd:+.4f} < {self._cfg.max_drawdown}"
                          f" (drawdown limit breached)")
        return False, "live track record within limits"

    def check_and_suspend(
        self,
        *,
        tracker: ShadowTracker,
        strategy_id: str,
        version: str,
        service: "StrategyGovernanceService",
        decided_by: str = "drift_monitor",
    ) -> tuple[bool, str]:
        """Check the live record and suspend via the service if drifting."""
        track = tracker.live_track_record(
            strategy_id=strategy_id, recent_n=self._cfg.recent_n,
        )
        suspend, reason = self.should_suspend(track)
        if not suspend:
            return False, reason
        try:
            service.suspend(strategy_id=strategy_id, version=version,
                            decided_by=decided_by, reason=reason)
        except Exception as e:  # noqa: BLE001
            return False, f"suspend attempted but failed: {e}"
        return True, reason
