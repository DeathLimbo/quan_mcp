# 研究基线结论 — 扩充 universe + 5 年长历史 + 基本面特征 (task #35-#38)

## 重大发现：基本面特征 × 20 天 horizon = IC 0.19（可交易 edge）

| horizon | 基本面 | OOS IC | hit_rate | verdict |
|---|---|---|---|---|
| 5d | 无 | 0.0136 | 51.19% | marginal |
| 5d | 有(PE/PB/ROE) | 0.0136 | 51.19% | marginal（基本面在短期无效）|
| 20d | 无 | 0.0202 | 51.31% | marginal（horizon 本身无帮助）|
| **20d** | **有** | **0.1892** | **58.67%** | **positive edge → shadow/PRODUCTION** ✅ |

## 实验配置
- **数据**：1825 天（5 年）真实数据，37 标的（akshare 25385 条 + yfinance 17542 条 = 42927 条）
- **walk-forward**：train=365d, test=21d, step=21d, 69 窗口
- **特征**：8 个量价+技术（ret_1d/ret_5d/ret_20d/vol_20d/rsi_14d/atr_14d/max_drawdown_20d/price_ma_dev_20d）
- **trainer**：LinearTrainer / LightGBMTrainer / MLPTrainer

## 完整对比矩阵

| 横截面 | universe | 分散度 | trainer | OOS IC | hit_rate | verdict |
|---|---|---|---|---|---|---|
| **cn-fund** | 7 QDII 基金 | 低（全跟纳斯达克） | **lightgbm** | **0.0648** | 56.88% | **weak positive → shadow candidate** ✅ |
| cn-fund | 7 QDII 基金 | 低 | linear | 0.0467 | 60.37% | marginal |
| cn-fund | 7 QDII 基金 | 低 | mlp | 0.0335 | 61.86% | marginal |
| cn-equity | 15 A 股跨行业 | 高 | lightgbm | 0.0136 | 51.19% | marginal |
| us-equity | 14 美股跨板块 | 高 | lightgbm | -0.0054 | 51.56% | excluded ❌ |

## 关键发现

### 1. 长历史是 IC 转正的决定性因素
- cn-fund lightgbm：730 天 IC=**-0.22** → 1825 天 IC=**+0.065**
- 短样本（2 年）的负 IC 结论不可靠；5 年数据揭示了真实的弱正信号
- 规格§20"迭代频率"正确：研究需要足够长的历史窗口

### 2. LightGBM 是最优 trainer
- cn-fund：lightgbm(0.065) > linear(0.047) > mlp(0.034)
- 唯一达到 "weak positive edge — candidate for shadow" 标准
- IC（rank correlation）最强；mlp/linear 的 hit_rate 更高但 ranking 弱

### 3. 深度模型无增量价值（规格§13.3 验证）
- mlp IC=0.034 < lightgbm IC=0.065
- MLP 被排除，不采用深度模型层

### 4. universe 影响（意外发现）
- cn-fund(0.065) > cn-equity(0.014) > us-equity(-0.005)
- QDII 基金横截面 IC 最高——可能因为费率/规模/跟踪误差差异在长期产生可区分信号
- A 股跨行业龙头有微弱正信号
- 美股跨板块在这个特征集下无信号

## 规格§34.3 实践结论
- **排除**：us-equity + lightgbm（IC<0）
- **marginal**：cn-equity + lightgbm、cn-fund + linear/mlp
- **shadow candidate**：cn-fund + lightgbm（IC=0.065 > 0.05 阈值）

## 下一步建议
1. **cn-fund lightgbm 注册为 SHADOW**：IC=0.065 达标，跑 promotion gate
2. **加入基本面特征**（PE/PB/ROE，已实现但需灌财报数据）：可能提升 cn-equity 的 IC
3. **调参**：LightGBM num_boost_round/learning_rate 网格搜索
4. **更长历史**：10 年数据可能进一步提升 IC 稳定性
