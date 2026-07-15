"""§81.1 candidate-vs-baseline promotion gate.

Spec §81.1 mandates that a CANDIDATE model must strictly beat every declared
baseline (BuyAndHold / FixedDCA / MovingAverage / CrossSectionMomentum /
RuleCluster) on the agreed metric set (IC and net return by default) before it
may transition to SHADOW or PRODUCTION.

The gate is a pure function over already-computed metrics. Training pipelines
compute walk-forward metrics for the candidate + each baseline on the same
holdout and pass them here; failure raises ``PromotionGateFailed`` and audit
must record the rejection.

No numpy / pandas dependency; downstream services can call this without
pulling in heavy ML infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from packages.common.errors import QuantError


class PromotionGateFailed(QuantError):
    """Candidate failed to strictly beat one or more baselines."""


# Canonical spec §81.1 gate keys. "ic" = Spearman IC on holdout; "net_return"
# = geometric net return after fees. Both are "higher is better". Callers may
# override with any monotone-higher-better metric names.
DEFAULT_GATE_KEYS: tuple[str, ...] = ("ic", "net_return")


@dataclass(frozen=True, slots=True)
class PromotionResult:
    """Outcome of the gate. ``passed`` implies candidate beat *every* baseline
    on *every* required key strictly (candidate > baseline)."""

    passed: bool
    candidate_id: str
    losses: tuple[tuple[str, str, float, float], ...] = ()
    # Each loss is (baseline_id, metric_key, candidate_value, baseline_value).
    keys_checked: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "candidate_id": self.candidate_id,
            "keys_checked": list(self.keys_checked),
            "losses": [
                {"baseline_id": b, "metric": k,
                 "candidate": c, "baseline": v}
                for b, k, c, v in self.losses
            ],
        }


def beats_all_baselines(
    *,
    candidate_id: str,
    candidate_metrics: Mapping[str, float],
    baselines: Mapping[str, Mapping[str, float]],
    keys: Sequence[str] = DEFAULT_GATE_KEYS,
) -> PromotionResult:
    """Return a PromotionResult; strict > on every ``keys`` vs every baseline.

    Args:
        candidate_id: model_id of the candidate under evaluation.
        candidate_metrics: mapping metric_key -> float on the *same* holdout
            slice used for the baselines.
        baselines: mapping baseline_id -> mapping metric_key -> float.
        keys: metric keys that must all improve. Default = ("ic","net_return").

    A missing metric on either side counts as a loss with -inf value on that
    side (i.e. candidate can only beat a baseline if the metric is present).
    """
    losses: list[tuple[str, str, float, float]] = []
    for baseline_id, base_metrics in baselines.items():
        for key in keys:
            c = candidate_metrics.get(key)
            b = base_metrics.get(key)
            # Absent metric => treat as -inf-equivalent; equal values fail.
            c_val = float("-inf") if c is None else float(c)
            b_val = float("-inf") if b is None else float(b)
            if not (c_val > b_val):
                losses.append((baseline_id, key, c_val, b_val))
    return PromotionResult(
        passed=(not losses),
        candidate_id=candidate_id,
        losses=tuple(losses),
        keys_checked=tuple(keys),
    )


def require_beats_all_baselines(**kwargs) -> PromotionResult:
    """Same as beats_all_baselines but raises PromotionGateFailed on failure.

    Use in ``models.registry`` transition hooks so the state-machine cannot
    move a candidate forward without gate evidence.
    """
    result = beats_all_baselines(**kwargs)
    if not result.passed:
        detail = "; ".join(
            f"{b}:{k} candidate={c:.6f} baseline={v:.6f}"
            for b, k, c, v in result.losses
        )
        raise PromotionGateFailed(
            f"candidate {result.candidate_id} did not beat baselines: {detail}"
        )
    return result
