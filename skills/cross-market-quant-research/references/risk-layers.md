# Risk Layers (fixed order, first REJECT wins)

| # | Layer | Typical REJECT codes | User-facing explanation |
|---|---|---|---|
| 1 | permission | `PERM_DENIED` | Your account is not entitled to trade this market. |
| 2 | regulatory | `NO_SHORT_CN` | Short-selling CN cash equities is not permitted for this account. |
| 3 | market_state | `HALTED`, `DELISTED`, `MARKET_CLOSED` | The instrument or market is not tradable today. |
| 4 | price_limit_and_liquidity | `AT_UPPER_LIMIT`, `AT_LOWER_LIMIT`, `LIQUIDITY_THIN` (review) | Price already at daily limit, or the order size exceeds 10% of 20-day ADV. |
| 5 | exposure_limits | `NAME_LIMIT`, `GROSS_LIMIT` | The trade would breach a per-name or gross-exposure cap. |
| 6 | stress_delta | `STRESS_LIMIT` | The scenario shock exceeds the tolerance ceiling. |
| 7 | operational | `KILL_SWITCH`, `DUP_INTENT` | Ops has disabled trading or the same intent was already submitted. |
| 8 | execution_feasibility | `MIN_LOT`, `TICK_ROUND` (review) | Quantity is not a whole-lot multiple, or price is off-tick. |

## Response guidance
- On **REJECT**, cite the layer, code, and reason. Do NOT propose "reduce size and retry" unless the code is `NAME_LIMIT` / `GROSS_LIMIT` / `LIQUIDITY_THIN`.
- On **REVIEW**, tell the user it needs a human check; do not silently proceed.
- On **ACCEPT**, still surface the risk trace so the user can see what was checked.
