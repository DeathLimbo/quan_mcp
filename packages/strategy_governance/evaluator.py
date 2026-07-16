"""StrategyEvaluator — deterministic, reproducible strategy evaluation (Phase 3).

Composes existing capabilities (LightGBMTrainer, build_dataset,
information_coefficient, beats_all_baselines) into a nested walk-forward
evaluation that answers: "does this strategy beat baselines out-of-sample,
across regimes, after costs?" — NOT "does it fit history".

Key design (issue #10 review §1 StrategyEvaluator):
  * Nested walk-forward: multiple non-overlapping train→predict folds; the
    prediction window never overlaps the training window (no in-sample leak).
  * Regime slicing: each test fold is tagged bull/bear/range by its realised
    drift; per-regime IC proves robustness across environments, not just on
    average.
  * Cost + drawdown: fold returns are net of commission+slippage; the equity
    curve yields max_drawdown and Sharpe.
  * repro_hash: deterministic hash of (data range, features, params, horizon,
    train_min, step) so two runs over the same frozen inputs produce the same
    hash — a failed reproduction is a data/code drift signal.
  * Baseline gate: candidate metrics vs declared baselines via
    beats_all_baselines (§81.1).

No assumption that "history == future". The walk-forward validates that the
strategy survived multiple regimes; the live Shadow/Canary phases (Phase 5)
validate the current regime.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from packages.data_sources.contracts import Bar
from packages.datasets.builder import build_dataset
from packages.evaluation.metrics import information_coefficient
from packages.evaluation.promotion import beats_all_baselines
from packages.training.lightgbm_trainer import LightGBMTrainer


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One out-of-sample fold."""
    test_start: str
    test_end: str
    n_test: int
    ic: float
    hit_rate: float
    mean_pred: float
    mean_actual: float
    regime: str            # bull / bear / range


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Aggregate walk-forward evaluation outcome."""
    n_folds: int
    n_total_predictions: int
    ic: float              # pooled rank IC across all folds
    hit_rate: float        # fraction of correct-direction predictions
    rmse: float            # root mean squared error of predicted returns
    mean_pred_return: float
    mean_actual_return: float
    regime_ic: dict[str, float] = field(default_factory=dict)  # per-regime IC
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    net_return: float = 0.0
    repro_hash: str = ""
    folds: tuple[WalkForwardFold, ...] = ()
    baseline_gate_passed: bool | None = None
    baseline_losses: tuple[tuple[str, str, float, float], ...] = ()

    def as_metrics(self) -> dict[str, float]:
        """Flat metrics dict for EvaluationRun.metrics."""
        m = {
            "ic": self.ic, "hit_rate": self.hit_rate, "rmse": self.rmse,
            "mean_pred_return": self.mean_pred_return,
            "mean_actual_return": self.mean_actual_return,
            "max_drawdown": self.max_drawdown, "sharpe": self.sharpe,
            "net_return": self.net_return, "n_folds": float(self.n_folds),
            "n_predictions": float(self.n_total_predictions),
        }
        for regime, ic in self.regime_ic.items():
            m[f"ic_{regime}"] = ic
        return m


# --------------------------------------------------------------------------- #
# Regime classification
# --------------------------------------------------------------------------- #
def classify_regime(realised_return: float, bull_threshold: float = 0.02,
                    bear_threshold: float = -0.02) -> str:
    """Tag a test window by its realised drift."""
    if realised_return >= bull_threshold:
        return "bull"
    if realised_return <= bear_threshold:
        return "bear"
    return "range"


# --------------------------------------------------------------------------- #
# Cost / drawdown helpers
# --------------------------------------------------------------------------- #
def _max_drawdown(equity: Sequence[float]) -> float:
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


def _sharpe(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StrategyEvaluator:
    """Runs nested walk-forward over a strategy's universe.

    ``train_min`` is the minimum rows before the first fold's test window.
    ``step`` is how many rows the test window advances per fold (use
    ``horizon_days`` for non-overlapping folds).
    """

    feature_names: tuple[str, ...]
    horizon_days: int
    train_min: int = 200
    step: int | None = None       # default = horizon_days (non-overlapping)
    num_boost_round: int = 30
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    bull_threshold: float = 0.02
    bear_threshold: float = -0.02

    def evaluate(
        self,
        bars: Sequence[Bar],
        *,
        strategy_id: str,
        version: str,
        params: dict[str, Any] | None = None,
        baseline_metrics: dict[str, dict[str, float]] | None = None,
        gate_keys: tuple[str, ...] = ("ic", "net_return"),
    ) -> EvaluationResult:
        """Run nested walk-forward. Returns aggregate metrics + per-fold detail.

        ``baseline_metrics`` maps baseline_id -> {metric: value}; if supplied,
        the candidate must beat every baseline on every gate key (§81.1).
        """
        step = self.step or self.horizon_days
        params = params or {}
        repro = self._repro_hash(bars, params)

        rows = build_dataset(
            bars, list(self.feature_names),
            horizon_days=self.horizon_days,
            start=date(1970, 1, 1), end=date(2100, 1, 1),
        )
        rows.sort(key=lambda r: r.as_of_date)

        folds: list[WalkForwardFold] = []
        all_pred: list[float] = []
        all_actual: list[float] = []
        fold_net_returns: list[float] = []   # for equity curve
        regime_preds: dict[str, list[float]] = {"bull": [], "bear": [], "range": []}
        regime_actuals: dict[str, list[float]] = {"bull": [], "bear": [], "range": []}

        T = self.train_min
        trainer = LightGBMTrainer(
            feature_names=list(self.feature_names),
            horizon_days=self.horizon_days,
            task="regression",
            num_boost_round=self.num_boost_round,
        )
        fold_idx = 0
        while T + self.horizon_days <= len(rows):
            train_rows = rows[:T]
            test_rows = rows[T:T + self.horizon_days]
            if len(train_rows) < self.train_min or len(test_rows) < 2:
                break
            try:
                model = trainer.fit(
                    train_rows, model_id=f"{strategy_id}_wf",
                    version=f"f{fold_idx}",
                )
            except Exception:
                # a fold that fails to train is skipped, not fatal
                T += step
                fold_idx += 1
                continue

            preds: list[float] = []
            actuals: list[float] = []
            for r in test_rows:
                try:
                    p = model.predict_return(r.features)
                except Exception:
                    p = 0.0
                preds.append(float(p) if p is not None else 0.0)
                actuals.append(float(r.label) if r.label is not None else 0.0)

            fold_ic = information_coefficient(preds, actuals) if len(preds) > 1 else 0.0
            hits = sum(1 for p, a in zip(preds, actuals)
                       if (p > 0) == (a > 0))
            hr = hits / len(preds) if preds else 0.0
            mean_pred = sum(preds) / len(preds) if preds else 0.0
            mean_actual = sum(actuals) / len(actuals) if actuals else 0.0
            realised = (sum(actuals) / len(actuals)) if actuals else 0.0
            regime = classify_regime(
                realised, self.bull_threshold, self.bear_threshold)

            folds.append(WalkForwardFold(
                test_start=test_rows[0].as_of_date.isoformat(),
                test_end=test_rows[-1].as_of_date.isoformat(),
                n_test=len(test_rows), ic=fold_ic, hit_rate=hr,
                mean_pred=mean_pred, mean_actual=mean_actual, regime=regime,
            ))
            all_pred.extend(preds)
            all_actual.extend(actuals)
            regime_preds[regime].extend(preds)
            regime_actuals[regime].extend(actuals)

            # fold net return: long when mean_pred > 0, cost-adjusted
            cost = (self.commission_bps + self.slippage_bps) / 1e4
            direction = 1.0 if mean_pred > 0 else (-1.0 if mean_pred < 0 else 0.0)
            fold_ret = direction * realised - cost
            fold_net_returns.append(fold_ret)

            T += step
            fold_idx += 1

        # pooled metrics
        ic = information_coefficient(all_pred, all_actual) if len(all_pred) > 1 else 0.0
        n = len(all_pred)
        hit = (sum(1 for p, a in zip(all_pred, all_actual) if (p > 0) == (a > 0))
               / n if n else 0.0)
        rmse = (math.sqrt(sum((p - a) ** 2 for p, a in zip(all_pred, all_actual)) / n)
                if n else 0.0)
        mean_pred_all = sum(all_pred) / n if n else 0.0
        mean_actual_all = sum(all_actual) / n if n else 0.0

        # per-regime IC
        regime_ic: dict[str, float] = {}
        for reg in ("bull", "bear", "range"):
            ps, acs = regime_preds[reg], regime_actuals[reg]
            if len(ps) > 1:
                regime_ic[reg] = information_coefficient(ps, acs)

        # equity curve from fold net returns
        equity = [1.0]
        for r in fold_net_returns:
            equity.append(equity[-1] * (1.0 + r))
        mdd = _max_drawdown(equity)
        sharpe = _sharpe(fold_net_returns)
        net_return = equity[-1] - 1.0 if equity else 0.0

        # baseline gate (§81.1)
        gate_passed: bool | None = None
        losses: tuple[tuple[str, str, float, float], ...] = ()
        if baseline_metrics:
            cand = {"ic": ic, "net_return": net_return}
            result = beats_all_baselines(
                candidate_id=strategy_id, candidate_metrics=cand,
                baselines=baseline_metrics, keys=gate_keys,
            )
            gate_passed = result.passed
            losses = result.losses

        return EvaluationResult(
            n_folds=len(folds), n_total_predictions=n, ic=ic, hit_rate=hit,
            rmse=rmse, mean_pred_return=mean_pred_all,
            mean_actual_return=mean_actual_all, regime_ic=regime_ic,
            max_drawdown=mdd, sharpe=sharpe, net_return=net_return,
            repro_hash=repro, folds=tuple(folds),
            baseline_gate_passed=gate_passed, baseline_losses=losses,
        )

    # ------------------------------------------------------------------ #
    def _repro_hash(self, bars: Sequence[Bar], params: dict[str, Any]) -> str:
        """Deterministic hash of frozen inputs. Same inputs => same hash."""
        if not bars:
            return ""
        first = bars[0].market_local_date.isoformat()
        last = bars[-1].market_local_date.isoformat()
        n = len(bars)
        payload = json.dumps({
            "first_date": first, "last_date": last, "n_bars": n,
            "first_close": str(bars[0].close), "last_close": str(bars[-1].close),
            "features": list(self.feature_names),
            "horizon": self.horizon_days, "train_min": self.train_min,
            "step": self.step or self.horizon_days,
            "num_boost_round": self.num_boost_round,
            "params": params,
        }, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()
