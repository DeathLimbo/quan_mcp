"""Feature engineering.

顶层约束：**一份特征代码，训练与推理共用**。
Features are registered via ``@feature`` and looked up by name; both training
pipelines and online inference call ``FeatureSet.compute(bars, as_of=...)``.
Training-only or inference-only feature code is a spec violation.
"""
from packages.features.registry import feature, FeatureSpec, FeatureRegistry, registry
from packages.features.featureset import FeatureSet, compute_features
from packages.features import basics  # noqa: F401  ensure builtins are registered
from packages.features import technical  # noqa: F401  ensure technical features registered

__all__ = ["feature", "FeatureSpec", "FeatureRegistry", "registry",
           "FeatureSet", "compute_features"]
