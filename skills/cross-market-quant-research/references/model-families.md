# Model families — quick reference

Six families defined in [families.py](file:///e:/workspace/quan/packages/models/families.py):

| Family id                       | Market   | Task                          | Horizons  | Baselines                          |
|---------------------------------|----------|-------------------------------|-----------|------------------------------------|
| `CN_FUND_LONG_A`                | CN       | Long quality + DCA multiplier | 60/120d   | BuyAndHold, FixedDCA               |
| `CN_ETF_SHORT_C`                | CN       | 5d direction/return/DD        | 5d        | MovingAverage_5_20, BuyAndHold     |
| `CN_EQUITY_CROSS_SECTION_B`     | CN       | 5/20d cross-section ranking   | 5/20d     | CrossSectionMomentum_20, B&H       |
| `US_EQUITY_CROSS_SECTION_B`     | US       | 5/20d cross-section ranking   | 5/20d     | CrossSectionMomentum_20, B&H       |
| `US_ETF_LONG_A_OR_SHORT_C`      | US       | ETF long/short                | 5/60d     | BuyAndHold, MovingAverage_5_20     |
| `MARKET_REGIME`                 | CN/US    | 6-state regime classification | 20d       | RuleCluster                        |

Rules:
- Cross-market share of `PRODUCTION` models is forbidden.
- Every candidate must beat all listed baselines on IC + net return before
  the registry accepts a `CANDIDATE → PRODUCTION` transition.
- `MARKET_REGIME` never emits trades directly; consumers read the regime
  score and gate exposure.

## Executor rules (§92)

- [CnExecutionModel](file:///e:/workspace/quan/packages/backtest/execution.py) —
  T+1 sell restriction, up/down-limit lock, halt/delisting NoFill.
- [UsExecutionModel](file:///e:/workspace/quan/packages/backtest/execution.py) —
  REGULAR-session fill with spread+slippage, split/dividend via
  `bar.adj_factor`, delisting hard-stop.
- [FundExecutionModel](file:///e:/workspace/quan/packages/backtest/execution.py) —
  15:00 unknown-price cutoff, subscription/redemption fees with
  holding-period brackets, `min_holding_days` violation returns NoFill.
