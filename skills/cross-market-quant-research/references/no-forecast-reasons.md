# NO_FORECAST Reasons and How to Respond

The `score` tool returns either a `Forecast` or a `NoForecast` (fail-closed).
Never invent a recommendation when NO_FORECAST is returned. Report the reason
to the user verbatim, then propose a safe next step.

| Reason | Meaning | What to tell the user | What NOT to do |
|---|---|---|---|
| `no_production_model` | No PRODUCTION model for that market+horizon | "No production model has been approved for this market and horizon. I can't score." | Do not fall back to a SHADOW/CANDIDATE model. |
| `missing_feature` | One or more inputs are unavailable at `as_of` | "Feature `X` is missing at as_of `T`. This may be a data outage or the requested date is before enough history." | Do not backfill or impute. |
| `no_artifact` | Metadata exists but model binary is not attached | "Model artifact is missing in the registry — this is an ops issue." | Do not use another model. |
| `feature_hash_mismatch` | The service's FeatureSet differs from the trained one | "The feature-set has drifted from what the model was trained on. Re-training or artifact update is required." | Do not paper over — this is a training-serving skew guardrail. |
| `insufficient_history` | Not enough bars for the required lookback | "This instrument has only N bars; the model needs M." | Do not extrapolate. |

## Response template

```
Score is unavailable for {instrument_id} at {as_of}.
Reason: {reason}
Detail: {detail}

Recommended next step: {one of}
- Contact ops if this is unexpected (missing_feature / no_artifact).
- Wait for the ingestion watermark to advance (missing_feature bounded to today).
- Choose a different horizon or market (no_production_model).
```
