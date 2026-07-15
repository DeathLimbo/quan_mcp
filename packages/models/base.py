from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Prediction:
    score: float          # calibrated in [0,1] for classifier heads; else raw
    horizon_days: int
    model_id: str
    model_version: str
    feature_set_hash: str


@runtime_checkable
class Model(Protocol):
    model_id: str
    version: str

    def predict_one(self, features: dict[str, float | None]) -> Prediction: ...
