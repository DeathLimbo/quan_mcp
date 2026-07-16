"""Training pipeline. Phase 2 provides:
- A trivial linear-regression trainer (numpy-free, pure Python) so the pipeline
  is exercisable without sklearn/lightgbm.
- LightGBM gradient-boosting trainer (spec §13 second-tier ML).
- Walk-forward rolling-window pipeline (spec §15.1).
- Champion-Challenger workflow: trainer emits a ``ModelRecord`` in DRAFT and a
  concrete ``Model`` instance for shadow evaluation.

Note: probability calibration (isotonic/Platt) lives in ``packages.models.isotonic``
(not here) to avoid a circular import with the model registry.

Real trainers plug into the same interface (LightGBM/XGBoost/sklearn) via
``fit(X, y) -> Model``.
"""
from packages.training.trainer import LinearTrainer, TrainedLinearModel, prepare_matrix
from packages.training.lightgbm_trainer import LightGBMTrainer, TrainedLightGBMModel
from packages.training.deep_trainer import (
    MLPTrainer, TrainedMLPModel, LSTMTrainer, TrainedLSTMModel,
)
from packages.training.walkforward import (
    OOSPrediction, WalkForwardResult, walk_forward,
)

__all__ = [
    "LinearTrainer", "TrainedLinearModel", "prepare_matrix",
    "LightGBMTrainer", "TrainedLightGBMModel",
    "MLPTrainer", "TrainedMLPModel", "LSTMTrainer", "TrainedLSTMModel",
    "OOSPrediction", "WalkForwardResult", "walk_forward",
]
