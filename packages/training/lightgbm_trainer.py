"""LightGBM trainer — spec §13 second-tier traditional ML.

Implements the same ``fit(rows) -> Model`` interface as :class:`LinearTrainer`
but uses LightGBM gradient boosting. Two task modes:

- ``classification`` (default): label binarised to ``forward_return > 0 -> 1``;
  ``predict_one`` returns the positive-class probability in [0, 1].
- ``regression``: predicts raw forward return; ``predict_one`` squashes via
  logistic to a [0, 1] score for compatibility with the ``Prediction.score``
  contract used by the inference service / risk engine.

The trained model satisfies :class:`packages.models.base.Model` and can be
attached to a ``ModelRecord`` via ``registry.register(rec, artifact=model)``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction
from packages.training.trainer import prepare_matrix

# lightgbm imported lazily so the training package stays importable without the
# native lib (libomp on macOS); LightGBMTrainer.__init__ triggers the import.

# Conservative defaults; callers override via **lgb_params.
_DEFAULT_CLF_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 5,
    "min_data_in_leaf": 20,
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
    "force_col_wise": True,
}

_DEFAULT_REG_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "metric": "rmse",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 5,
    "min_data_in_leaf": 20,
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
    "force_col_wise": True,
}


@dataclass
class TrainedLightGBMModel:
    """LightGBM model satisfying the :class:`Model` protocol."""

    model_id: str
    version: str
    feature_names: tuple[str, ...]
    booster: object  # lightgbm.Booster
    horizon_days: int
    feature_set_hash: str
    task: str  # "classification" | "regression"

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        vec: list[float] = []
        for n in self.feature_names:
            v = features.get(n)
            if v is None:
                raise FeatureMissingError(f"feature {n} missing at inference")
            vec.append(float(v))
        raw = float(self.booster.predict([vec])[0])
        if self.task == "classification":
            score = max(0.0, min(1.0, raw))
        else:
            # regression: squash raw return to [0,1] via logistic
            import math
            score = 1.0 / (1.0 + math.exp(-raw)) if raw != 0 else 0.5
        return Prediction(
            score=score,
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )

    def predict_return(self, features: dict[str, float | None]) -> float | None:
        """Raw forward-return prediction (regression only); None for classifiers.

        Returns the model's expected horizon-day return (e.g. +0.023 = +2.3%),
        unsquashed — unlike ``predict_one`` which logistic-squashes regression
        output to [0,1] for the inference-service score contract. Use this when
        the caller wants the return magnitude, not just direction probability.
        """
        if self.task != "regression":
            return None
        vec: list[float] = []
        for n in self.feature_names:
            v = features.get(n)
            if v is None:
                raise FeatureMissingError(f"feature {n} missing at inference")
            vec.append(float(v))
        return float(self.booster.predict([vec])[0])

    def save(self, path: str) -> None:
        """Serialize booster to ``{path}.lgb`` and metadata to ``{path}.json``."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(p.with_suffix(".lgb")))
        meta = {
            "model_id": self.model_id,
            "version": self.version,
            "feature_names": list(self.feature_names),
            "horizon_days": self.horizon_days,
            "feature_set_hash": self.feature_set_hash,
            "task": self.task,
        }
        p.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "TrainedLightGBMModel":
        """Reload from ``{path}.lgb`` + ``{path}.json`` written by :meth:`save`."""
        import lightgbm as lgb
        p = Path(path)
        meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
        booster = lgb.Booster(model_file=str(p.with_suffix(".lgb")))
        return cls(
            model_id=meta["model_id"],
            version=meta["version"],
            feature_names=tuple(meta["feature_names"]),
            booster=booster,
            horizon_days=meta["horizon_days"],
            feature_set_hash=meta["feature_set_hash"],
            task=meta["task"],
        )


class LightGBMTrainer:
    """Gradient-boosting trainer. Requires the ``lightgbm`` dependency."""

    def __init__(
        self,
        feature_names: list[str],
        horizon_days: int,
        *,
        task: str = "classification",
        num_boost_round: int = 200,
        **lgb_params: Any,
    ) -> None:
        if task not in ("classification", "regression"):
            raise ValueError(f"task must be 'classification' or 'regression', got {task!r}")
        self.feature_names = list(feature_names)
        self.horizon_days = horizon_days
        self.task = task
        self.num_boost_round = num_boost_round
        if task == "classification":
            self._params = {**_DEFAULT_CLF_PARAMS, **lgb_params}
        else:
            self._params = {**_DEFAULT_REG_PARAMS, **lgb_params}

    def fit(
        self,
        rows: list[DatasetRow],
        *,
        model_id: str,
        version: str | None = None,
        valid_rows: list[DatasetRow] | None = None,
    ) -> TrainedLightGBMModel:
        X, y = prepare_matrix(rows, self.feature_names)
        if self.task == "classification":
            y = [1.0 if v > 0 else 0.0 for v in y]
        import numpy as np
        import lightgbm as lgb
        X = np.array(X, dtype=np.float64)
        y = np.array(y, dtype=np.float64)
        train_set = lgb.Dataset(X, label=y, feature_name=list(self.feature_names))

        valid_sets: list[lgb.Dataset] = []
        valid_names: list[str] = []
        if valid_rows is not None:
            Xv, yv = prepare_matrix(valid_rows, self.feature_names)
            if self.task == "classification":
                yv = [1.0 if v > 0 else 0.0 for v in yv]
            Xv = np.array(Xv, dtype=np.float64)
            yv = np.array(yv, dtype=np.float64)
            valid_sets.append(lgb.Dataset(Xv, label=yv, feature_name=list(self.feature_names)))
            valid_names.append("valid")

        booster = lgb.train(
            self._params,
            train_set,
            num_boost_round=self.num_boost_round,
            valid_sets=valid_sets or None,
            valid_names=valid_names or None,
            callbacks=[lgb.log_evaluation(0)] if valid_sets else None,
        )

        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|{feature_set_hash}|{utcnow().isoformat()}".encode()
        ).hexdigest()[:12]
        return TrainedLightGBMModel(
            model_id=model_id,
            version=ver,
            feature_names=tuple(self.feature_names),
            booster=booster,
            horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash,
            task=self.task,
        )
