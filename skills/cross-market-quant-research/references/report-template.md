# Research Note — {instrument.canonical}

**As-of:** {as_of_iso}  &nbsp; **Horizon:** {horizon_days}d  &nbsp; **Reviewer:** _human sign-off required_

## 1. Instrument
- Name: {name}
- Market / Venue / Asset: {market} / {venue} / {asset_type}
- Listing date: {listed_at}

## 2. Forecast
- Model: {model_id}@{model_version}
- Feature-set hash: `{feature_hash}`
- Score: **{score:+.4f}**
- Interpretation (calibrated, 0..1 for classifier heads): {interpretation}

> If the tool returned NO_FORECAST, replace this whole section with the reason
> and STOP; do not proceed to trade intent.

## 3. Context
- Last 20 sessions: mean return {ret20_mean:+.2%}, vol {vol20:.2%}
- Momentum 60/5: {mom60_5:+.4f}
- Dollar volume 20d: {dollar_vol_20d:.0f}
- Recent corporate actions: {corp_actions_or_none}

## 4. Trade intent (optional)
- Side / Qty / Ref price: {side} / {quantity} / {ref_price}
- Risk verdict: **{risk_verdict}**
- Risk trace:
{risk_markdown}

## 5. Rationale (plain language)
{one_paragraph}

## 6. Caveats
- Point-in-time data only; no look-ahead.
- Ranking/probability is *not* an execution instruction.
- Any live trade requires the ops runbook and dual-control approval.
