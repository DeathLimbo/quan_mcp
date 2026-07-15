"""Training pipeline. Phase 2 provides:
- A trivial linear-regression trainer (numpy-free, pure Python) so the pipeline
  is exercisable without sklearn/lightgbm.
- Champion-Challenger workflow: trainer emits a ``ModelRecord`` in DRAFT and a
  concrete ``Model`` instance for shadow evaluation.

Real trainers plug into the same interface (LightGBM/XGBoost/sklearn) via
``fit(X, y) -> Model``.
"""
from packages.training.trainer import LinearTrainer, TrainedLinearModel, prepare_matrix

__all__ = ["LinearTrainer", "TrainedLinearModel", "prepare_matrix"]
