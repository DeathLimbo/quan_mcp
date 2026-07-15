# A 股、美股、基金与 ETF 量化 Agent：Skill + MCP 完整落地与模块实施规格

> 版本：V4.0（工程实施版）  
> 规则核对日期：2026-07-15；生产环境必须通过版本化规则服务动态读取最新交易规则  
> 适用范围：中国公募基金、场内 ETF/LOF、A 股股票、美国上市普通股与 ETF；长线定投、中短线择时、股票横截面研究和跨市场组合风险管理  
> 推荐部署形态：一个跨市场 Quant Skill + `quant-read-mcp` + `quant-admin-mcp` + A 股/美股市场适配器 + 独立量化后端  
> 建设目标：形成“多市场建库 → 点时点研究 → 分市场训练 → 自动更新 → 定期迭代 → 概率预测 → 风险决策 → 自动复盘”的闭环系统  
> 参考方法论：Ernest P. Chan《量化交易：如何建立自己的算法交易事业》（Quantitative Trading: How to Build Your Own Algorithmic Trading Business）

---

## 0. 方案评审结论

### 0.1 总体结论

原方案的主流程——建库、训练模型、自动更新、定期迭代、输出预测和自动复盘——方向正确。加入 A 股和美股后，系统必须升级为真正的**多市场量化平台**，而不能只给原有股票模型增加一个 `market=US` 参数。

必须满足以下约束：

1. **不能承诺完美预测或保证收益。** 市场包含随机波动、制度变化、突发消息、流动性冲击和参与者反馈。验收目标应是概率校准、样本外稳定、风险受控和能够拒绝预测。
2. **A 股与美股必须使用独立模型族、规则快照和回测执行器。** 两个市场在交易日历、交易时段、币种、公司披露、价格约束、停牌机制、结算、卖空、公司行为和数据许可方面均不同。
3. **统一接口不等于统一模型。** Skill 和 MCP 可以统一，但后端应至少存在 `CN_A`、`US_EQUITY`、`CN_FUND`、`CN_ETF`、`US_ETF` 等任务域；每个任务域独立训练、验证、发布和回滚。
4. **所有数据必须具备 point-in-time 可用时间。** 中国财务数据按实际公告时间进入模型；美国财务数据按 SEC 申报被接收或公开的时间进入模型。不得用报告期末日期代替信息可用时间。
5. **时间必须同时保存 UTC 与市场本地时间。** A 股使用 `Asia/Shanghai`；美股使用 `America/New_York` 并正确处理夏令时。不得把固定的北京时间或韩国时间写死为美股开盘时间。
6. **组合必须区分本币收益与基准币种收益。** 美股本地 USD 收益、USD/CNY 或 USD/KRW 汇率变化以及费用，需要分别记录和归因。
7. **Skill 仍然只作为控制平面。** Skill 管理流程、边界、校验和解释；数据库、ETL、训练、回测、推理、风控、任务调度和交易执行运行在独立服务中。
8. **MCP 仍然只作为受控能力网关。** 不向 Agent 暴露任意 SQL、Shell、Python、模型发布或无限额下单工具。
9. **真实交易必须与研究、预测和模型管理隔离。** 第一阶段只做研究、预测、模拟盘和人工确认；跨市场真实交易还必须额外处理经纪商能力、换汇、税费、账户权限和市场准入。

推荐最终形态：

```text
跨市场 Quant Skill
        ↓
quant-read-mcp / quant-admin-mcp
        ↓
统一接口层 + CN-A Adapter + US Adapter
        ↓
数据、特征、训练、回测、推理、风控、审计服务
        ↓
PostgreSQL / 时序库 / 对象存储 / 模型注册 / 任务队列
```

### 0.2 可以保证什么

系统可以通过工程和治理机制保证：

- 每条预测包含市场、交易所、数据截止时间、模型版本、数据快照、规则版本和交易日历版本。
- 所有时间戳可在 UTC、市场本地时间和用户展示时区之间一致转换。
- 数据过期、时区错位、公司行为未处理、模型异常或风险超限时停止给出买入建议。
- A 股预测只调用 A 股生产模型，美股预测只调用美股生产模型；跨市场模型必须单独通过验证。
- 所有建议经过确定性的风险引擎，并计入组合币种风险。
- 每次预测、建议、人工确认、模拟成交和实际结果均可追溯。
- 模型只能通过样本外测试、影子运行和审批后进入生产。
- 同一数据快照、规则版本和模型版本能够复现同一预测结果，允许浮点容差。

### 0.3 不能保证什么

系统不能保证：

- 某只 A 股或美股未来一定上涨。
- 每日推荐均盈利。
- 同一个模型在两个市场都有效。
- 在所有市场状态下长期保持同一胜率。
- 避免所有突发政策、财务重述、欺诈、停牌、交易中断、跳空和流动性事件。
- 回测收益一定能复制到实盘。
- 汇率变化不会抵消美股本地收益。

正确目标：

> 在严格防止数据泄漏、计入市场规则、交易成本和汇率影响，并经过分市场样本外验证的前提下，输出经过校准的概率预测；在不确定性过高时拒绝交易，通过仓位、组合约束和模型降级控制不可承受的损失。

## 1. 项目定位

本项目不是依靠大语言模型直接猜涨跌的聊天机器人，而是一个多市场量化投研与决策系统：

1. **多市场数据平台**：保存中国基金/ETF、A 股、美股/ETF、指数、宏观、汇率、财报、公司行为、持仓和预测记录。
2. **市场规则与日历平台**：管理不同交易所、板块、时段、节假日、夏令时、价格约束、交易单位和结算规则。
3. **量化研究平台**：进行 point-in-time 特征工程、分市场回测、参数检验、模型训练和策略比较。
4. **组合与风险系统**：将预测转化为定投倍数、候选排名、建议仓位、币种暴露和退出规则。
5. **量化 Agent**：负责编排工作流、调用模型、解释结构化结果、生成晨报和组织复盘。
6. **模型治理系统**：发现数据异常、市场漂移和策略失效，在通过验证后发布或回滚模型。

核心原则：

- 模型产生概率、收益区间、风险和排名，不直接决定最终仓位。
- 风控引擎决定允许的仓位和动作。
- Agent 只能解释和编排，不能改写模型数字或绕过风控。
- A 股与美股使用独立模型卡、数据卡和回测假设。
- 真实资金交易必须经历回测、样本外测试、影子运行、模拟盘和小资金人工确认。

## 2. 策略域与资产范围

### 2.1 长线定投 A：基金与 ETF

适用品种：

- 中国宽基、风格、行业、债券、黄金等公募基金和 ETF。
- 美国宽基、行业、债券、商品等 ETF；美国共同基金不作为第一版重点。
- 经白名单批准的主动股票基金。

主要周期：20、60、120 个交易日以及一年以上资产配置周期。

核心目标：

- 判断继续持有、提高定投、降低定投、暂停新增或替换。
- 估计收益区间、下行风险和组合边际风险贡献。
- 区分标的本地货币收益与用户基准币种收益。
- 识别跨市场重复暴露，例如中国科技基金与美国大型科技 ETF 的共同因子风险。

### 2.2 ETF 中短线策略 C

适用品种：

- 流动性良好的中国场内 ETF/LOF。
- 流动性良好的美国上市 ETF。
- 宽基、行业、债券、黄金和其他经批准的 ETF。

主要周期：下一交易日、未来 3/5/10 个本地交易时段。

要求：

- 中国 ETF 和美国 ETF 分市场训练，不直接共享一个生产模型。
- 计入本地交易成本、买卖价差、溢折价、交易时段和市场休市差异。
- 美国 ETF 第一版只使用常规交易时段；盘前、盘后和隔夜交易默认关闭。
- 系统必须允许输出 `NO_TRADE`。

### 2.3 A 股股票策略 B-CN

支持范围：

- 上海证券交易所、深圳证券交易所和北京证券交易所中经白名单批准的 A 股。
- 主板、科创板、创业板和北交所使用不同的板块规则配置。
- 第一版优先覆盖流动性较好的非 ST 普通股；其他板块在规则和成本验证通过后逐步开放。

必须处理：

- 原始、前复权、后复权价格与总回报因子。
- 分红、送转、配股、拆并股等公司行为。
- ST/*ST、停复牌、涨跌停、退市整理、上市初期特殊规则和交易单位。
- 财务报表、业绩预告、公告和重大事项的实际发布时间。
- 历史成分、已退市股票和不可成交状态，防止存活者偏差。
- 行业、市值、风格、流动性和事件风险中性化。

当前交易规则只作为配置示例，不应写死在模型代码中。上交所公开页面显示，主板与科创板在交易单位、价格限制和上市初期规则方面不同；系统必须按 `market_rule_version` 获取当日适用规则。

推荐产品形态：

```text
股票池过滤
  ↓
行业、市值和流动性中性化
  ↓
价值、质量、成长、动量、低波、反转和事件特征
  ↓
预测未来 5/10/20 个交易日的横截面排名与下行风险
  ↓
构建分散 Top-N 组合，而不是押注单只股票点位
```

### 2.4 美股股票策略 B-US

支持范围：

- NYSE、Nasdaq、NYSE Arca、NYSE American 等经数据源和经纪商支持的美国上市普通股与 ETF。
- 第一版优先覆盖大中盘普通股和高流动性 ETF。
- ADR、REIT、BDC、SPAC 等单独分类，并由白名单决定是否进入模型。

第一版不支持：

- OTC/Pink Sheet。
- 极低价、极低流动性和无法可靠估计成本的微盘股。
- 期权、权证、复杂结构产品。
- 高频、逐笔、做市和超低延迟策略。
- 未经单独回测的盘前、盘后或隔夜交易。

必须处理：

- 交易所、主上市地、股票类别、ticker 历史、CIK 和公司身份映射。
- 股票拆分、反向拆分、现金/股票股息、并购、分拆、退市和 ticker 变更。
- 10-K、10-Q、8-K 等披露的实际可用时间以及后续重述。
- 常规交易时段、提前收盘、市场休市、交易暂停和异常成交。
- USD 本地收益、基准币种收益和汇率贡献。
- 行业分类、规模、质量、盈利能力、投资、动量、估值和事件风险。

美国证券市场自 2024-05-28 起，对多数适用证券采用 T+1 标准结算周期；这属于结算规则，不应被错误实现为 A 股式“买入后次日才能卖出”的交易限制。回测器必须区分**交易可卖约束**和**资金/证券结算约束**。

### 2.5 跨市场组合策略 D

跨市场层不直接预测每只股票，而负责：

- A 股、美国股票、基金、ETF 和现金之间的风险预算。
- CNY、USD 以及用户基准币种的汇率暴露。
- 中国与美国节假日不一致导致的隔夜和无法同步调仓风险。
- 全球风险状态、利率、美元、商品和波动率等共同因子。
- 相关性突变和危机阶段的风险收缩。

跨市场特征必须严格遵守信息可用时间。例如：美国前一交易日收盘信息可以用于下一次 A 股开盘前预测；A 股当日收盘信息可以用于随后美股常规开盘前预测。必须由 UTC 时间戳判断，不可仅按日期连接。

### 2.6 不采用一个“万能股票模型”

推荐模型族：

```text
CN_FUND_LONG_A
CN_ETF_SHORT_C
US_ETF_LONG_A_OR_SHORT_C
CN_EQUITY_CROSS_SECTION_B
US_EQUITY_CROSS_SECTION_B
GLOBAL_PORTFOLIO_D
```

可以共享特征计算框架、接口和模型代码模板，但每个模型族必须独立：

- 定义股票池。
- 训练和验证。
- 做概率校准。
- 估计成本和容量。
- 发布、影子运行和回滚。

## 3. 总体闭环

```text
用户持仓 / 策略配置 / 市场白名单
                  │
                  ▼
┌────────────────────────────────────────┐
│ 1. 市场日历、规则、标的与身份解析       │
│ CN-A / US / Fund / ETF / FX            │
└──────────────────┬─────────────────────┘
                   ▼
┌────────────────────────────────────────┐
│ 2. 多源数据采集、点时点存储和质量检查   │
└──────────────────┬─────────────────────┘
                   ▼
┌────────────────────────────────────────┐
│ 3. 分市场特征工程                       │
│ CN-Fund / CN-ETF / CN-Equity / US      │
└──────────────────┬─────────────────────┘
                   ▼
       ┌───────────┼───────────┬──────────────┐
       ▼           ▼           ▼              ▼
   长线 A       ETF C      A股 B-CN       美股 B-US
       └───────────┴───────────┴──────────────┘
                   ▼
┌────────────────────────────────────────┐
│ 4. 跨市场组合、汇率和风险决策引擎       │
└──────────────────┬─────────────────────┘
                   ▼
┌────────────────────────────────────────┐
│ 5. Agent 报告、解释、通知和人工审批     │
└──────────────────┬─────────────────────┘
                   ▼
┌────────────────────────────────────────┐
│ 6. 结果回填、自动复盘、漂移与归因       │
└──────────────────┬─────────────────────┘
                   ▼
┌────────────────────────────────────────┐
│ 7. 分市场候选模型训练、影子运行与发布   │
└────────────────────────────────────────┘
```

## 3.1 推荐的 Skill + MCP + 后端架构

```text
┌──────────────────────────────────────────────────────────┐
│ ChatGPT / Agent Host                                      │
│                                                          │
│ Cross-Market Quant Skill                                  │
│ - 识别市场、资产、时区、目标和预测周期                    │
│ - 强制检查数据截止、市场会话和规则版本                    │
│ - 编排 MCP 工具调用                                       │
│ - 校验输出并解释风险                                      │
└────────────────────────┬─────────────────────────────────┘
                         │ MCP
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────────┐
│ quant-read-mcp       │      │ quant-admin-mcp          │
│ 查询、预测、风险、记录│      │ 训练、影子、发布、回滚    │
└───────────┬──────────┘      └────────────┬─────────────┘
            └──────────────┬───────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│ Unified Quant API / Job Queue                             │
├──────────────────────────────────────────────────────────┤
│ CN-A Adapter │ US Adapter │ Fund Adapter │ FX Adapter     │
├──────────────────────────────────────────────────────────┤
│ data / feature / research / model / inference / risk     │
│ portfolio / audit / scheduler / notification services    │
└────────────────────────┬─────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────┐
│ PostgreSQL / TimescaleDB / Redis / Object Storage        │
│ MLflow / Queue / Metrics / Logs / Secrets                │
└──────────────────────────────────────────────────────────┘
```

### 3.1.1 Skill 的职责

Skill 保存“如何安全、正确地使用量化系统”：

- 什么请求触发本 Skill。
- 如何解析 A 股、美股、基金和 ETF 标的。
- 必填市场、交易所、时区、币种、预测时点和风险配置。
- 不同市场和周期应调用哪个生产模型。
- MCP 工具调用顺序。
- 如何检查数据、规则、日历、模型和汇率状态。
- 什么情况下输出 `NO_FORECAST`、`NO_TRADE` 或 `RESEARCH_ONLY`。
- 如何调用确定性风险引擎。
- 如何生成 A 股晨报、美股盘前报告和跨市场组合报告。
- 禁止语言模型自行编造行情、仓位、汇率、止损线或交易规则。

Skill 不保存历史行情、实时缓存、模型二进制、密钥、训练进程、调度状态或券商凭据。

### 3.1.2 MCP 的职责

MCP 负责向 Agent 暴露受控的 Tools 和 Resources：

- 市场日历和规则快照。
- 标的解析和公司身份映射。
- 点时点数据、特征和模型卡。
- 生产推理、股票池排名和风险评估。
- 预测留痕、报告读取和管理任务提交。

MCP 不实现数据库、模型算法、任务调度、交易所接入或最终风险政策；这些由后端服务实现。

### 3.1.3 为什么仍建议两个 MCP，而不是按市场拆多个 Skill

推荐：

- **一个跨市场 Quant Skill**：统一输入、审计、拒绝预测、风险披露和报告规范。
- **一个日常 `quant-read-mcp`**：内部路由到 CN-A、US、Fund 和 FX Adapter。
- **一个高权限 `quant-admin-mcp`**：用于训练、发布和回滚。

不建议第一版创建“A 股 Skill”和“美股 Skill”两套重复工作流，否则容易出现两套不一致的风险规则和输出合同。只有在团队、权限、合规主体或基础设施完全分离时，才考虑按市场拆 MCP Server。

### 3.1.4 MCP 与内部 API 的关系

```text
Agent → MCP Tool → Unified Quant API → Market Adapter → Quant Service
```

禁止向 Agent 暴露通用 SQL、任意 Python、任意文件读取或任意订单提交。

### 3.1.5 定时任务不由 Skill 触发

每日 ETL、盘前推理、收盘复盘、月度候选训练和漂移监控由 Airflow、n8n 或任务队列主动运行。即使用户没有打开聊天，系统也必须继续更新和监控。

## 3.2 多市场抽象层

### 3.2.1 规范化市场编码

建议使用 MIC 或内部稳定编码：

```text
CN.XSHG.EQ.600519
CN.XSHE.EQ.000001
CN.XSHG.ETF.510300
US.XNAS.EQ.AAPL
US.XNYS.EQ.BRK.B
US.ARCX.ETF.SPY
```

不得仅用 `600519` 或 `AAPL` 作为数据库唯一键。ticker 会变化，同一公司也可能有多个股票类别或多地上市。

### 3.2.2 时间规范

每条时序记录至少保存：

- `event_time_utc`
- `event_time_local`
- `market_timezone`
- `trade_date_local`
- `available_at_utc`
- `ingested_at_utc`

A 股使用 `Asia/Shanghai`。美股使用 `America/New_York`，由 IANA 时区数据库处理夏令时。用户可在 `Asia/Seoul` 等展示时区查看报告，但展示时区不能影响训练数据连接。

### 3.2.3 币种规范

每条持仓和收益同时保存：

- 本地币种：CNY 或 USD。
- 组合基准币种：由用户配置，可为 CNY、USD、KRW 等。
- 汇率数据截止时间和来源。
- 本地资产收益、汇率收益和基准币种总收益。

精确换算：

```text
base_return = (1 + local_asset_return) × (1 + fx_return) - 1
```

### 3.2.4 规则服务

交易时段、价格限制、交易单位、提前收盘、结算、可卖约束和特殊板块规则必须由版本化规则服务返回：

```text
market_rule_version = CN_XSHG_MAIN_2026_01
calendar_version    = XSHG_2026_V2
```

回测、推理和风险决策都要保存对应版本，防止规则变化后无法复现历史结果。

# 第一阶段：建库

## 4. 建库目标

数据库必须同时支持四种工作负载：

1. 原始数据留档。
2. 量化研究和特征计算。
3. 线上模型推理。
4. 预测结果、交易决策和实际结果复盘。

推荐技术：

- PostgreSQL：业务、配置、模型和预测记录。
- TimescaleDB：可选，用于大量日线或分钟线时序数据。
- Redis：缓存最新数据、任务状态和模型推理结果。
- MinIO：可选，保存模型文件、训练数据快照和回测报告。
- MLflow：保存实验、指标、参数和模型版本。

第一版可以只使用 PostgreSQL + 本地模型目录，系统稳定后再加入 TimescaleDB、MinIO 和 MLflow。

---

## 5. 数据分层

### 5.1 ODS 原始数据层

原样追加保存：

- 中国基金资料、净值、持仓披露、费用和申赎状态。
- A 股与美股原始行情、报价摘要和成交量。
- 指数、ETF 份额、估值和成分历史。
- 中国上市公司公告和财务披露。
- 美国 SEC 申报元数据、财务事实和文档引用。
- 公司行为、ticker/代码变化、上市、停牌、退市和交易暂停。
- 市场日历、交易时段、提前收盘和规则版本。
- CNY、USD 以及组合基准币种汇率。
- 利率、宏观、商品和全球风险代理变量。

ODS 原则：只追加、不静默覆盖；保存来源、抓取时间、内容摘要和许可标签。

### 5.2 DWD 清洗明细层

完成：

- 标的身份、交易所和 ticker 历史统一。
- UTC、本地时间、交易日和可用时间统一。
- 原始价格、拆分调整价、总回报价和复权因子统一。
- 公司行为和财务重述关联。
- 市场日历与交易时段对齐。
- 缺失、异常、冲突和延迟标记。
- 数据源优先级与许可范围合并。

### 5.3 DWS 特征层

按“市场 + 标的 + 可用时点 + 特征版本”保存：

- 趋势、动量、反转、波动、回撤和流动性。
- 估值、质量、成长、盈利能力和投资因子。
- 行业、市值、风格和横截面排名。
- 事件、公告、财报、盈利意外和重述标记。
- A 股交易状态、涨跌停距离和停牌风险。
- 美股财报日、交易暂停、拆股和盘后事件风险。
- 宏观、利率、美元、商品和跨市场上下文。
- 汇率及本地/基准币种收益特征。

### 5.4 ADS 应用层

直接面向 Agent 和报告：

- A 股盘前市场状态、股票池排名和风险预警。
- 美股盘前市场状态、股票池排名和财报事件风险。
- 中国基金/ETF 长线评分和定投建议。
- 美国 ETF 配置和择时建议。
- 跨市场资产配置、币种暴露和组合风险。
- 模型预测、风险决策、复盘和漂移指标。

## 6. 核心表设计

### 6.1 市场主表 `market`

```sql
CREATE TABLE market (
    market_id           BIGSERIAL PRIMARY KEY,
    market_code         VARCHAR(32) NOT NULL UNIQUE,
    exchange_mic        VARCHAR(8),
    country_code        VARCHAR(2) NOT NULL,
    timezone_name       VARCHAR(64) NOT NULL,
    default_currency    VARCHAR(8) NOT NULL,
    calendar_id         VARCHAR(64) NOT NULL,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

示例：`CN_A`、`CN_FUND`、`US_EQUITY`、`US_ETF`，以及 `XSHG`、`XSHE`、`XBSE`、`XNYS`、`XNAS`、`ARCX`。

### 6.2 标的主表 `instrument`

```sql
CREATE TABLE instrument (
    instrument_id       BIGSERIAL PRIMARY KEY,
    canonical_id        VARCHAR(64) NOT NULL UNIQUE,
    market_id           BIGINT NOT NULL REFERENCES market(market_id),
    exchange_mic        VARCHAR(8),
    local_symbol        VARCHAR(32) NOT NULL,
    instrument_name     VARCHAR(256) NOT NULL,
    instrument_type     VARCHAR(32) NOT NULL,
    security_subtype    VARCHAR(32),
    currency            VARCHAR(8) NOT NULL,
    primary_listing     BOOLEAN DEFAULT TRUE,
    listed_at           TIMESTAMPTZ,
    delisted_at         TIMESTAMPTZ,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (market_id, exchange_mic, local_symbol)
);
```

`instrument_type` 示例：`CN_FUND`、`ETF`、`COMMON_STOCK`、`ADR`、`REIT`、`INDEX`、`FX`、`CASH`。

### 6.3 标识符历史表 `instrument_identifier`

用于保存 ticker 变更、CIK、ISIN、CUSIP（如数据许可允许）、中国证券代码和供应商 ID：

```sql
CREATE TABLE instrument_identifier (
    instrument_id       BIGINT NOT NULL REFERENCES instrument(instrument_id),
    identifier_type     VARCHAR(32) NOT NULL,
    identifier_value    VARCHAR(128) NOT NULL,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_to            TIMESTAMPTZ,
    source              VARCHAR(32) NOT NULL,
    PRIMARY KEY (identifier_type, identifier_value, valid_from)
);
```

### 6.4 市场会话表 `market_session`

```sql
CREATE TABLE market_session (
    market_id           BIGINT NOT NULL REFERENCES market(market_id),
    trade_date_local    DATE NOT NULL,
    session_type        VARCHAR(32) NOT NULL,
    open_at_utc         TIMESTAMPTZ,
    close_at_utc        TIMESTAMPTZ,
    is_trading_day      BOOLEAN NOT NULL,
    is_early_close      BOOLEAN DEFAULT FALSE,
    calendar_version    VARCHAR(64) NOT NULL,
    PRIMARY KEY (market_id, trade_date_local, session_type, calendar_version)
);
```

### 6.5 市场规则表 `market_rule_snapshot`

保存某交易日适用的：

- 价格限制或停牌机制。
- 买卖单位和最小报价单位。
- 买入后可卖约束。
- 结算周期。
- 板块、ST、新股和特殊证券规则。
- 常规、盘前、盘后和提前收盘权限。

规则必须可版本化、可审计，不得散落在策略代码中。

### 6.6 行情表 `market_bar`

```sql
CREATE TABLE market_bar (
    instrument_id       BIGINT NOT NULL REFERENCES instrument(instrument_id),
    bar_start_utc       TIMESTAMPTZ NOT NULL,
    trade_date_local    DATE NOT NULL,
    session_type        VARCHAR(32) NOT NULL,
    bar_interval        VARCHAR(16) NOT NULL,
    open_price          NUMERIC(24, 10),
    high_price          NUMERIC(24, 10),
    low_price           NUMERIC(24, 10),
    close_price         NUMERIC(24, 10),
    volume              NUMERIC(32, 8),
    amount_local        NUMERIC(32, 8),
    vwap                NUMERIC(24, 10),
    source              VARCHAR(32) NOT NULL,
    quality_status      VARCHAR(16) DEFAULT 'NORMAL',
    available_at_utc    TIMESTAMPTZ NOT NULL,
    ingested_at_utc     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, bar_start_utc, bar_interval, session_type, source)
);
```

调整价格不直接覆盖原始行情，而通过公司行为和调整因子生成版本化视图。

### 6.7 公司行为表 `corporate_action`

保存：

- 现金/股票股息。
- 拆股、反向拆股、送转、配股。
- 并购、分拆、换股、私有化和退市。
- ticker、交易所和股票类别变化。
- 公告、除权、记录和支付日期。
- 数据公布时间、修订版本和来源。

### 6.8 点时点财务事实表 `fundamental_fact`

```sql
CREATE TABLE fundamental_fact (
    instrument_id       BIGINT NOT NULL REFERENCES instrument(instrument_id),
    metric_name         VARCHAR(128) NOT NULL,
    fiscal_period_end   DATE,
    filing_type         VARCHAR(32),
    filed_at_utc        TIMESTAMPTZ NOT NULL,
    available_at_utc    TIMESTAMPTZ NOT NULL,
    value_numeric       NUMERIC(38, 10),
    unit                VARCHAR(32),
    revision_no         INTEGER DEFAULT 0,
    source              VARCHAR(32) NOT NULL,
    source_document_id  VARCHAR(128),
    PRIMARY KEY (
        instrument_id, metric_name, fiscal_period_end,
        available_at_utc, revision_no, source
    )
);
```

训练集查询必须使用 `available_at_utc <= prediction_as_of_utc`。

### 6.9 基金净值表 `fund_nav_daily`

保留原方案字段，并增加：

- `available_at_utc`
- `valuation_currency`
- `nav_status`
- `source_version`

### 6.10 汇率表 `fx_rate`

```sql
CREATE TABLE fx_rate (
    base_currency       VARCHAR(8) NOT NULL,
    quote_currency      VARCHAR(8) NOT NULL,
    rate_time_utc       TIMESTAMPTZ NOT NULL,
    rate_type           VARCHAR(32) NOT NULL,
    rate_value          NUMERIC(24, 10) NOT NULL,
    source              VARCHAR(32) NOT NULL,
    available_at_utc    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (base_currency, quote_currency, rate_time_utc, rate_type, source)
);
```

### 6.11 用户持仓表 `portfolio_position`

增加：

- 本地币种市值和组合基准币种市值。
- 账户、经纪商、市场和可用现金分桶。
- 税批次或成本批次（如需要）。
- 已结算与未结算资金/证券状态。
- 数据快照时间和来源。

### 6.12 特征表 `feature_value`

主键应至少包含：

```text
instrument_id + as_of_utc + feature_name + feature_version + dataset_snapshot_id
```

并保存 `available_at_utc`，禁止只按自然日覆盖。

### 6.13 模型注册表 `model_registry`

增加：

- `market_scope`
- `exchange_scope`
- `asset_universe_id`
- `base_currency`
- `calendar_version`
- `rule_version`
- `data_license_manifest_id`
- `probability_calibration_id`
- `ood_detector_id`

状态：`CANDIDATE / SHADOW / PRODUCTION / SUSPENDED / RETIRED`。

### 6.14 预测表 `model_prediction`

```sql
CREATE TABLE model_prediction (
    prediction_id       BIGSERIAL PRIMARY KEY,
    request_id          VARCHAR(64) NOT NULL UNIQUE,
    as_of_utc           TIMESTAMPTZ NOT NULL,
    data_cutoff_utc     TIMESTAMPTZ NOT NULL,
    target_session_date DATE NOT NULL,
    instrument_id       BIGINT NOT NULL REFERENCES instrument(instrument_id),
    market_code         VARCHAR(32) NOT NULL,
    model_id            BIGINT NOT NULL,
    horizon_sessions    INTEGER NOT NULL,
    expected_return_local DOUBLE PRECISION,
    expected_return_base  DOUBLE PRECISION,
    up_probability      DOUBLE PRECISION,
    drawdown_probability DOUBLE PRECISION,
    lower_bound         DOUBLE PRECISION,
    upper_bound         DOUBLE PRECISION,
    feature_snapshot_id VARCHAR(64) NOT NULL,
    calendar_version    VARCHAR(64) NOT NULL,
    rule_version        VARCHAR(64) NOT NULL,
    fx_snapshot_id      VARCHAR(64),
    forecast_status     VARCHAR(32) NOT NULL,
    created_at_utc      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

### 6.15 决策与复盘表

`strategy_decision` 额外保存：

- 市场、会话、币种和经纪商约束。
- 本地仓位、基准币种风险和 FX 风险。
- 交易规则拒绝原因。
- 财报/公告/停牌/涨跌停等事件风险。

`prediction_evaluation` 分别记录：

- 本地资产收益。
- 汇率收益。
- 基准币种总收益。
- 相对本地基准和全球基准的超额收益。
- 是否可实际成交以及执行偏差。

## 7. 数据源与许可原则

### 7.1 共通原则

1. 核心行情至少准备一个生产数据源和一个校验源。
2. 保存原始响应摘要、数据版本、许可标签和更新时间。
3. 不允许 Agent 使用搜索引擎零散价格作为正式模型输入。
4. 免费接口仅用于原型；生产数据源必须评估稳定性、历史修订、退市覆盖、延迟和再分发许可。
5. 训练数据快照必须记录其可使用范围，避免把只允许展示的数据用于模型训练或对外分发。

### 7.2 A 股与中国基金

优先级：

- 交易所、基金公司、指数公司、官方披露平台和经许可的数据供应商。
- 原型阶段可使用 AKShare、Tushare 等适配器，但不能作为唯一生产来源。
- 财务和公告必须记录实际公告时间；基金持仓必须记录实际披露时间。
- 必须覆盖已退市证券、历史指数成分和历史证券简称/代码。

### 7.3 美股

生产数据至少需要：

- 经许可的 NYSE/Nasdaq/CTA/UTP 或聚合行情源。
- SEC EDGAR 申报与实际接收时间。
- 公司行为、ticker 历史和已退市证券。
- 交易日历、提前收盘和交易暂停数据。
- USD 与组合基准币种汇率。

注意：

- 不同供应商的 `adjusted close` 定义可能不同，必须自行重建并校验总回报因子。
- 只包含当前上市公司的数据会造成严重存活者偏差。
- 分析师预期、短仓、期权、实时盘口和新闻数据通常涉及额外许可；没有许可时不得把其列为必选特征。

### 7.4 数据源适配器合同

所有 Adapter 返回统一字段：

```json
{
  "source": "provider_name",
  "source_version": "2026-07",
  "event_time_utc": "...",
  "available_at_utc": "...",
  "ingested_at_utc": "...",
  "license_tag": "INTERNAL_RESEARCH",
  "quality_status": "NORMAL"
}
```

## 8. 数据质量检查

每日分市场检查：

- 最新交易日和市场日历是否一致。
- UTC、本地时间和夏令时转换是否正确。
- 提前收盘和跨市场节假日是否正确。
- OHLC、成交量、成交额和 VWAP 是否合理。
- 复权因子、拆股、分红、并购和 ticker 变更是否完整。
- 财报/公告是否按照实际可用时间进入特征。
- A 股停牌、ST、涨跌停和板块状态是否正确。
- 美股交易暂停、拆股、财报日和退市状态是否正确。
- 汇率是否与资产数据截止时间匹配。
- 同一标的多源差异是否超限。
- 特征是否存在无穷值、异常跳变或跨市场日期错配。
- 训练和推理使用的标的身份映射是否一致。

建议 fail-closed 规则：

```text
核心行情缺失或日历异常：NO_FORECAST
公司行为未完成对账：NO_FORECAST
点时点可用时间缺失：禁止将该字段用于训练和推理
A 股交易状态不明确：NO_TRADE
美股 ticker/CIK 身份冲突：NO_FORECAST
FX 数据过期：仅输出本地收益，禁止输出基准币种仓位建议
模型特征缺失率 > 阈值：NO_FORECAST
数据源冲突 > 市场配置阈值：人工检查
```

必须建立自动测试：

- 美股夏令时切换周。
- 中国休市而美国开市、美国休市而中国开市。
- 股票拆分、反向拆分和现金分红日。
- A 股一字涨跌停与停牌。
- 美股财报在盘前、盘后发布。
- ticker 变更和退市。

# 第二阶段：训练模型

## 9. 先设计策略，再选择模型

按照《量化交易：如何建立自己的算法交易事业》的核心方法，第一步不是寻找最复杂的模型，而是寻找：

- 逻辑合理的策略。
- 数据可获得的策略。
- 参数较少的策略。
- 容易回测和执行的策略。
- 与个人资金规模和交易频率匹配的策略。

复杂度不是优势。一个简单、稳定、扣除成本后仍有效的策略，通常优于历史拟合非常漂亮但无法解释的复杂系统。

---

## 10. 研究假设

每个市场、股票池和预测周期必须单独写研究卡片。

A 股示例：

```yaml
strategy_name: CN_A_EQUITY_XS_20D_V1
market: CN_A
universe: 非ST、非停牌、上市满一定交易日且流动性达标的A股
hypothesis: 经行业和市值中性化后，质量、估值、动量和低波因子组合可对未来20日横截面收益提供增量信息
prediction_horizon_sessions: 20
rebalance_frequency: weekly
execution_assumption: next_open_or_vwap
rule_snapshot_required: true
benchmark: 中证全指或股票池等权组合
expected_failure_regime:
  - 风格快速反转
  - 政策冲击
  - 大量涨跌停或流动性收缩
```

美股示例：

```yaml
strategy_name: US_EQUITY_XS_20D_V1
market: US_EQUITY
universe: NYSE/Nasdaq大中盘普通股，排除OTC和极低流动性标的
hypothesis: 经行业和规模中性化后，盈利能力、投资、估值、动量和盈利事件特征可改善未来20日横截面排序
prediction_horizon_sessions: 20
rebalance_frequency: weekly
execution_session: REGULAR
base_currency: configurable
benchmark: Russell 1000或批准股票池等权组合
expected_failure_regime:
  - 宏观利率突变
  - 财报季极端跳空
  - 因子拥挤快速反转
```

没有明确股票池、可用时间、执行时点、成本和失败场景的模型不得进入训练。

## 11. 长线定投 A 的模型体系

建议采用“评分模型 + 风险模型 + 市场状态模型”，而不是一个模型决定全部结论。

### 11.1 长期质量评分

评分维度：

| 类别 | 指标 |
|---|---|
| 收益质量 | 1/3/5 年年化收益、滚动收益稳定性 |
| 风险 | 最大回撤、下行波动率、尾部损失 |
| 风险收益 | 夏普、索提诺、卡玛 |
| 估值 | PE/PB 分位、股债收益差 |
| 风格 | 大小盘、价值成长、行业暴露 |
| 管理 | 基金经理任期、规模变化、换手 |
| 组合价值 | 与现有持仓相关性、风险贡献 |

示例：

```text
长期综合评分 =
20% 收益稳定性
+ 20% 最大回撤控制
+ 15% 风险调整后收益
+ 15% 当前估值
+ 10% 风格稳定性
+ 10% 基金管理稳定性
+ 10% 与现有组合的互补性
```

### 11.2 动态定投模型

基础定投金额记为 `B`：

```text
最终投入金额 = B × 估值系数 × 趋势系数 × 风险预算系数
```

示例系数：

| 状态 | 系数 |
|---|---:|
| 估值极低 | 1.50 |
| 估值偏低 | 1.20 |
| 估值正常 | 1.00 |
| 估值偏高 | 0.60 |
| 估值极高 | 0.20 |
| 趋势改善 | 1.10 |
| 趋势中性 | 1.00 |
| 趋势恶化 | 0.80 |
| 组合风险超限 | 0.00～0.70 |

必须设置最大和最小定投倍数，防止某一个指标造成极端投入。

### 11.3 长线预测目标

建议预测：

- 未来 20 日收益。
- 未来 60 日收益。
- 未来 120 日收益。
- 未来 60 日最大回撤。
- 未来 60 日收益为正的概率。
- 未来 60 日回撤超过 10% 的概率。
- 当前市场状态。

输出必须是概率或区间：

```json
{
  "expected_return_60d": 0.035,
  "return_interval_60d": [-0.075, 0.128],
  "positive_probability_60d": 0.62,
  "drawdown_over_10pct_probability": 0.17,
  "market_regime": "震荡偏强"
}
```

---

## 12. 短线策略 C 的模型体系

### 12.1 交易池过滤

进入模型前先过滤：

- 上市时间不足。
- 平均成交额过低。
- 买卖价差过大。
- 长期停牌或交易异常。
- 溢价率过高的跨境 ETF。
- 规模过小或清盘风险高。
- 数据不完整。
- 跟踪误差异常。

### 12.2 特征

趋势：

- MA5/10/20/60
- 均线斜率
- 价格距均线比例
- 突破强度
- MACD
- ADX

动量：

- 1/3/5/10/20/60 日收益
- 横截面动量排名
- 相对基准强弱
- 行业相对强弱

风险：

- ATR
- 历史波动率
- 下行波动率
- 5/20/60 日最大回撤
- 跳空
- 尾部收益

交易与资金：

- 成交额变化
- 换手率
- ETF 份额变化
- 买卖价差
- 溢价率
- 融资或市场情绪代理变量

宏观与跨市场：

- 沪深主要指数
- 国债收益率
- 美元指数
- 人民币汇率
- 美债利率
- 黄金
- 原油
- 海外股指
- 波动率指数代理变量

### 12.3 预测目标

同时训练三个模型：

1. **方向模型**：未来 5 日收益大于零或大于交易成本阈值的概率。
2. **收益模型**：未来 5 日预期收益。
3. **风险模型**：未来 5 日最大回撤超过阈值的概率。

最终评分：

```text
综合分 =
35% 方向概率
+ 30% 预期收益得分
+ 20% 趋势和相对强度
- 15% 回撤风险
```

决策示例：

| 综合分 | 动作 |
|---:|---|
| ≥ 0.75 | 允许小仓位买入 |
| 0.60～0.75 | 观察或试探仓 |
| 0.45～0.60 | 不操作 |
| < 0.45 | 规避或减仓 |

---


## 12.4 A 股横截面模型 B-CN

推荐分层：

1. **股票池过滤器**：上市天数、ST、停牌、退市状态、成交额、价格、可交易状态。
2. **因子模型**：价值、质量、成长、动量、反转、低波、流动性和事件。
3. **风险模型**：行业、市值、Beta、波动、相关性和组合边际风险。
4. **交易可实现性模型**：涨跌停距离、成交额容量、冲击成本和次日可卖约束。

训练目标优先使用：

- 未来 5/10/20 个交易日横截面收益分位。
- 相对行业或市场基准的超额收益。
- 未来最大回撤和不可成交风险。

评估重点：Rank IC、分层收益、Top-N 组合净收益、换手、涨跌停不可成交和退市收益。

## 12.5 美股横截面模型 B-US

推荐分层：

1. **身份与股票池过滤器**：主上市地、股票类别、ticker 历史、退市、价格和流动性。
2. **基本面模型**：盈利能力、质量、投资、成长、估值和财务重述。
3. **价格与事件模型**：动量、反转、波动、财报事件、拆股和交易暂停。
4. **风险模型**：行业、规模、Beta、利率敏感度、波动和拥挤度。
5. **币种层**：本地 USD 预测与组合基准币种转换分离。

财务特征只使用预测时点已经公开的申报版本。财报发布日期和 SEC 接收时间必须进入数据快照。

第一版推荐：

- 仅做 long-only 或低换手多空研究，不自动做空。
- 仅使用常规交易时段成交假设。
- 财报日前后设置事件风险上限或单独模型。
- 对 ADR、REIT 和金融股使用类别特定特征或单独模型。

## 12.6 跨市场上下文与收益换算

跨市场上下文可以作为辅助特征，但不能制造时间穿越：

```text
A股预测 as_of = 中国开盘前
可用：此前已完成的美国常规收盘、美元、利率和商品数据
不可用：随后才公布的中国或美国数据

美股预测 as_of = 美国常规开盘前
可用：当天已经完成的A股收盘、欧洲早盘和此前宏观数据
不可用：美股开盘后才产生的数据
```

模型应分别输出：

- `expected_return_local`
- `expected_fx_return`
- `expected_return_base`

除非汇率模型也通过验证，否则不得把汇率点预测包装成高置信度结论。

## 13. 模型选择顺序

### 第一层：基准规则

必须先建立：

- 买入并持有
- 固定定投
- 均线趋势
- 横截面动量
- 估值分位定投
- 波动率目标
- 风险平价

机器学习模型必须明显优于这些简单基准，才有上线价值。

### 第二层：传统机器学习

推荐：

- Logistic Regression
- Ridge / Lasso / Elastic Net
- Random Forest
- LightGBM
- XGBoost
- CatBoost

### 第三层：时序和深度模型

在数据量、基线和实验治理成熟后再考虑：

- LSTM
- TCN
- Transformer
- 多模型集成

不得因为模型更复杂就默认更先进。

---

## 14. 标签定义

标签以**本地交易时段**而不是自然日定义：

```python
future_return_h = close.shift(-h) / close - 1
label_up_h = (future_return_h > cost_and_hurdle).astype(int)
```

横截面标签建议使用：

```text
未来 h 个交易时段超额收益
未来 h 个交易时段收益分位
未来 h 个交易时段最大回撤
未来 h 个交易时段是否发生不可成交或交易暂停
```

多市场要求：

- A 股 `h=5` 表示未来五个 A 股交易日。
- 美股 `h=5` 表示未来五个美国交易日。
- 跨市场组合不能简单按自然日内连接，必须使用各自会话和 UTC 时间。
- 美股财报事实的 `available_at_utc` 必须早于 `as_of_utc`。
- A 股公告和基金持仓披露按实际公开时间使用。
- 基准币种标签必须使用当时可获得的 FX 数据和明确的计价规则。

## 15. 回测方法

### 15.1 分市场时间切分

禁止随机拆分。A 股与美股分别执行 expanding/rolling walk-forward；最终再在组合层合并结果。

要求：

- 最终测试集与调参隔离。
- 标签区间重叠时使用 purging 和 embargo。
- 不同市场使用各自交易日历。
- 跨市场特征通过 `available_at_utc` 校验。
- 每个市场至少覆盖上涨、下跌、震荡和流动性收缩阶段。

### 15.2 A 股执行模拟

必须模拟：

- 适用板块的价格限制和特殊上市阶段规则。
- 停牌、ST、退市整理和无法成交。
- 买入、卖出单位和买入后可卖约束。
- 一字涨跌停、成交量不足和价格冲击。
- 印花税、佣金、过户费等按账户和当期规则配置，不在代码中写死。

### 15.3 美股执行模拟

必须模拟：

- 常规交易时段和提前收盘。
- 拆股、分红、并购、ticker 变更和退市收益。
- 佣金、监管费、买卖价差和冲击成本。
- 交易暂停、开盘跳空和财报事件。
- T+1 结算对现金可用性的影响。
- 经纪商是否支持碎股、盘前盘后、做空和借券；V1 默认不使用这些能力。

### 15.4 共同偏差

检查：

- 存活者、前视、数据窥探、参数过拟合和标签泄漏。
- 财务重述和历史数据库回填造成的“修订后数据泄漏”。
- 只使用当前 ticker 或当前指数成分。
- 复权和总回报处理错误。
- 忽略无法成交、税费、汇率和资金结算。
- 反复查看最终测试集后继续调参。

### 15.5 组合级回测

跨市场组合需要：

- 本地币种和基准币种两套净值。
- FX 归因。
- 中国与美国休市错配。
- 不同市场开收盘顺序和调仓延迟。
- 分市场现金桶和未结算资金。
- 风险预算、相关性突变和压力情景。

## 16. 评价指标

所有指标必须同时按**市场、模型、股票池、市场状态和币种口径**分组。

收益：

- 本地币种累计/年化收益。
- 组合基准币种累计/年化收益。
- 本地基准和全球组合基准的超额收益。
- FX 贡献、资产贡献和交互项归因。
- 月度正收益比例和收益集中度。

风险：

- 最大回撤、波动率、下行波动率。
- VaR / CVaR、尾部损失和最大连续亏损。
- 市场、行业、规模、风格和币种暴露。
- 休市错配、跳空、财报/公告和流动性风险。

风险调整：

- 夏普、索提诺、卡玛和信息比率。
- 在统一风险预算下的净收益改善。

交易质量：

- 胜率、盈亏比和 Profit Factor。
- 换手率、平均持有期、单笔成本和容量。
- A 股不可成交、涨跌停和停牌比例。
- 美股开盘跳空、提前收盘和交易暂停影响。
- 预期成本与模拟/实际成本偏差。

预测质量：

- AUC、Log Loss、Brier Score、MAE / RMSE。
- IC / Rank IC、Top/Bottom 分层收益。
- 概率校准曲线和高置信度真实胜率。
- 预测区间覆盖率。
- OOD 覆盖率、模型分歧和拒绝预测比例。

组合质量：

- CN 与 US 子组合贡献。
- 市场和币种风险预算利用率。
- 相关性突变时的损失。
- `NO_TRADE` 和 `NO_FORECAST` 避免的损失。

模型上线不能只看准确率，也不能只看收益率；A 股和美股必须分别达标。

# 第三阶段：自动化更新数据库

## 17. 多市场自动调度

调度器不应写死北京时间或韩国时间，而应读取 `market_session`。

### 17.1 通用事件驱动规则

```text
session_open - 90m：确认市场日历、规则、数据和模型状态
session_open - 60m：生成盘前特征和生产推理
session_open - 30m：运行风险决策并生成报告
session_close + 15m：获取初步收盘数据
session_close + 60m：完成公司行为、净值和财务更新
session_close + 90m：回填预测结果并生成复盘
```

### 17.2 A 股任务

- 使用 `Asia/Shanghai` 交易日历。
- 盘前报告必须明确数据截止到前一可用交易时段。
- 收盘后更新 A 股、ETF、指数和基金净值；基金净值晚于股票收盘时可分批补齐。
- 中国节假日和调休不通过普通周一至周五规则推断。

### 17.3 美股任务

- 使用 `America/New_York` 交易日历和夏令时。
- NYSE 常规核心交易时段为 09:30–16:00 ET；报告任务基于会话时间计算，而非固定换算为北京时间或韩国时间。
- 提前收盘日使用日历服务动态调整。
- V1 只为常规交易时段生成正式交易建议；盘前/盘后数据可作为事件特征，但不自动执行。
- SEC 申报、财报和公司行为可在盘前或盘后到达，必须按实际时间增量更新。

### 17.4 跨市场任务

- 每日生成一次跨市场组合风险快照。
- 任一市场休市时，不强制同步调仓另一市场。
- 汇率和全球风险数据应在每个市场盘前分别冻结快照。
- 报告可显示为用户时区，例如 `Asia/Seoul`，但审计记录同时保存市场本地时间和 UTC。

### 17.5 周期任务

- 每日：数据、推理、结果回填和风险监控。
- 每周：分市场漂移、股票池和组合相关性。
- 每月：分市场候选模型训练和影子比较。
- 每季度：策略逻辑、数据许可、成本、容量和跨市场配置评审。

## 18. 自动化工具

推荐组合：

```text
n8n / Airflow
    ↓
Python ETL
    ↓
PostgreSQL / TimescaleDB
    ↓
Feature Pipeline
    ↓
FastAPI 推理服务
    ↓
Risk Engine
    ↓
Report Agent
    ↓
企业微信 / 钉钉 / 邮件
```

选择原则：

- 流程较少、需要可视化：n8n。
- 数据任务复杂、依赖关系多：Airflow。
- 第一版可以由 n8n 调度 Python 脚本。
- 模型训练和回测不应直接写在 n8n 节点内部，应由独立 Python 服务执行。

---

## 19. ETL 幂等性

所有自动任务必须支持重复执行：

- 行情唯一键使用 `instrument_id + bar_start_utc + bar_interval + session_type + source`。
- 财务事实使用 `instrument_id + metric + fiscal_period + available_at_utc + revision`。
- 公司行为使用稳定事件 ID、事件版本和来源。
- 数据写入使用 UPSERT 或不可变追加，不静默覆盖历史版本。
- 原始数据、清洗数据、特征和应用结果分层。
- 每个任务记录市场、会话、开始/结束时间、数据量、摘要和错误。
- 每次特征计算保存 `feature_version` 和 `dataset_snapshot_id`。
- 每次推理保存模型、日历、规则、FX 和特征快照。
- 报告发送使用幂等键，失败重跑不能重复推送。
- A 股任务失败不应阻塞美股任务，反之亦然；组合报告标记部分不可用状态。

# 第四阶段：定期使用新数据迭代模型

## 20. 迭代频率

推荐：

| 任务 | 频率 |
|---|---|
| 行情和净值更新 | 每个交易日 |
| 模型推理 | 每个交易日 |
| 预测结果回填 | 每个交易日 |
| 模型漂移检测 | 每周 |
| 候选模型训练 | 每月 |
| 完整策略评审 | 每季度 |
| 交易池和特征体系评审 | 每半年 |

不建议每天重新训练并直接替换生产模型。市场噪声可能导致模型不断追逐近期行情。

---

## 21. Champion–Challenger 机制

模型状态：

```text
CANDIDATE → SHADOW → PRODUCTION → RETIRED
```

- **CANDIDATE**：新训练模型。
- **SHADOW**：每天产生预测，但不参与正式决策。
- **PRODUCTION**：正式生产模型。
- **RETIRED**：历史模型，可用于回滚和审计。

候选模型必须满足：

1. 多个样本外窗口均有效。
2. 扣除成本后仍优于基准。
3. 最大回撤不明显恶化。
4. 概率校准合理。
5. 不是依赖单一品种。
6. 不是只在单一市场状态有效。
7. 特征数量和参数复杂度可接受。
8. 模拟盘或影子运行达到最低观察期。
9. 数据和代码可复现。
10. 风险评审通过。

---

## 22. 漂移检测

### 数据漂移

检查：

- PSI
- KS 检验
- 特征均值和方差变化
- 缺失率变化
- 极端值比例变化

### 预测漂移

检查：

- 预测概率分布变化
- 推荐数量突然变化
- 模型总是输出同一方向
- 置信度异常升高或降低

### 效果漂移

检查：

- 滚动胜率
- 滚动 AUC
- 滚动 IC
- 滚动夏普
- 滚动最大回撤
- 高置信度组真实胜率
- 实际收益与预测收益偏差

触发条件示例：

```text
连续 20 日 IC < 0
连续 30 日高置信度预测胜率低于 50%
滚动最大回撤超过回测阈值的 1.5 倍
模型收益连续 2 个月落后基准
核心特征 PSI > 0.25
```

触发后：

- 降低模型权重。
- 切换为保守规则模型。
- 暂停新增短线仓位。
- 启动原因分析。
- 不允许 Agent 自行重新上线未经验证的新模型。

---

# 第五阶段：输出预测

## 23. 预测输出结构

每条预测必须包含：

- `market_code`、`exchange_mic`、`canonical_instrument_id`
- `as_of_utc`、市场本地时间和用户展示时间
- `data_cutoff_utc`
- `calendar_version`、`rule_version`
- 模型、数据和特征快照版本
- 预测周期，单位为本地交易时段
- 本地币种上涨概率、预期收益和区间
- 基准币种预期收益及 FX 贡献（如可用）
- 回撤、事件、流动性和 OOD 风险
- 建议动作、允许仓位、持有周期和退出/失效条件
- `VALID / NO_FORECAST / NO_TRADE / RESEARCH_ONLY`

不得只输出“买入某股票”。

## 24. 报告体系

### 24.1 A 股盘前报告

```markdown
# A股量化盘前报告

市场：CN_A
数据截止：前一交易日收盘及盘前已公开数据
日历版本：XSHG_XSHE_2026_Vx
规则版本：CN_A_RULES_2026_Vx

## 市场状态
- 风格：大盘/小盘、价值/成长
- 波动与流动性风险
- 涨跌停和停牌风险概览

## 股票候选
- 预测周期：5/10/20 个交易日
- 横截面排名、上涨概率、预期超额收益
- 事件风险、行业和市值暴露
- 风控允许仓位或 NO_TRADE

## 基金与ETF
- 长线定投倍数
- ETF短线候选

## 组合风险
- 行业集中度、相关性和回撤预算
```

### 24.2 美股盘前报告

```markdown
# 美股量化盘前报告

市场：US_EQUITY
会话：REGULAR
数据截止：盘前冻结快照
时区：America/New_York / 用户展示时区
日历与规则版本：...

## 市场状态
- 指数、利率、美元、波动与风险偏好
- 当日财报和事件风险

## 股票与ETF候选
- 本地USD预期收益
- 基准币种预期收益和FX贡献
- 横截面排名、回撤概率、流动性和财报风险
- 风控允许仓位或 NO_TRADE

## 跨市场组合
- A股与美股共同因子暴露
- USD风险和现金桶
```

### 24.3 报告原则

- A 股和美股报告分开生成，避免数据截止时间混淆。
- 综合组合报告只汇总已经冻结的分市场预测。
- 没有足够优势时明确写“本次无新交易建议”。

## 25. Agent 的职责边界

Agent 可以：

- 通过批准的 MCP 工具查询市场状态、持仓、模型和风险结果。
- 编排数据状态检查、标的解析、生产推理、风险校验和预测留痕。
- 解释 A 股、美股、基金、ETF 和 FX 的结构化结果。
- 生成分市场盘前报告、跨市场组合报告和复盘。
- 提醒数据、时区、日历、规则、模型和组合异常。
- 创建待人工确认的研究或模拟交易建议。

Agent 不可以：

- 直接执行任意数据库查询、Shell 或 Python。
- 编造行情、财务、汇率、交易规则或市场会话。
- 自行改变模型、股票池、风险上限或规则版本。
- 将 A 股模型结果套用到美股，或反向套用。
- 因单条新闻或公告直接触发仓位。
- 未经审批使用真实资金、换汇或提交订单。
- 将自然语言判断当作最终交易信号。

---

# 第六阶段：自动复盘

## 26. 每次预测必须留痕

最小留痕示例：

```json
{
  "request_id": "pred_cn_xshg_600519_20260715_20s",
  "market": "CN_A",
  "canonical_instrument_id": "CN.XSHG.EQ.600519",
  "as_of_utc": "2026-07-15T00:20:00Z",
  "market_local_time": "2026-07-15T08:20:00+08:00",
  "data_cutoff_utc": "2026-07-14T07:00:00Z",
  "calendar_version": "XSHG_2026_V2",
  "rule_version": "CN_XSHG_MAIN_2026_V1",
  "model_id": "CN_EQ_XS_20D_V4",
  "dataset_snapshot_id": "cn_snap_20260714_close",
  "feature_snapshot_id": "cn_feat_20260715_preopen_v6",
  "horizon_sessions": 20,
  "prediction": {
    "up_probability": 0.63,
    "expected_return_local": 0.018,
    "drawdown_probability": 0.21,
    "rank_percentile": 0.84
  },
  "risk_decision": {
    "action": "BUY",
    "allowed_position": 0.02,
    "policy_version": "cross_market_risk_v4"
  },
  "forecast_status": "VALID"
}
```

到期后补充：

```json
{
  "actual_return_local": 0.012,
  "actual_fx_return": 0.0,
  "actual_return_base": 0.012,
  "actual_max_drawdown": -0.014,
  "benchmark_excess_return": 0.004,
  "direction_correct": true,
  "was_executable": true,
  "execution_block_reason": null
}
```

美股记录还要保存 `fx_snapshot_id`、财报事件标记、USD 本地收益和基准币种收益。

## 27. 复盘层级

### 每日

- A 股、美股、基金/ETF 分别统计预测和交易质量。
- 高置信度与低置信度分组表现。
- 本地收益、FX 和基准币种收益归因。
- 不可成交、停牌、交易暂停、跳空和成本偏差。
- 日历、规则、数据或工具异常。

### 每周

- 按市场状态、行业、规模、资产类别和币种分组。
- CN 与 US 模型漂移和概率校准。
- 特征贡献、股票池变化和 OOD 覆盖。
- 跨市场相关性和组合风险变化。

### 每月

- 各市场 Production 与 Shadow 模型比较。
- 策略净收益、风险、换手、成本和容量。
- FX 影响、参数稳定性和特征重要度变化。
- 是否需要重训、降权、暂停或回滚。

### 每季度

- 分市场策略经济逻辑是否仍成立。
- 收益是否集中在少数时期、标的或币种。
- 数据许可、经纪商能力和真实容量。
- 跨市场资产配置和风险预算是否需要调整。

# 第七阶段：风险管理

## 28. 强制风险规则

风险政策按市场和组合两级配置，不允许 LLM 修改。

```yaml
portfolio:
  base_currency: configurable
  max_total_equity_ratio: 0.70
  max_cn_a_ratio: configurable
  max_us_equity_ratio: configurable
  max_single_currency_unhedged_ratio: configurable
  max_single_stock_ratio: 0.05
  max_single_etf_ratio: 0.10
  max_sector_ratio: configurable

cn_a:
  reject_st_or_suspended_by_default: true
  enforce_board_rule_snapshot: true
  enforce_sell_availability: true
  reject_one_price_limit_without_liquidity: true
  max_daily_new_position_ratio: 0.08

us_equity:
  regular_session_only_v1: true
  reject_otc: true
  reject_unresolved_ticker_identity: true
  earnings_event_position_cap: configurable
  max_daily_new_position_ratio: 0.08

model:
  min_buy_probability: configurable_by_model
  min_rank_score: configurable_by_model
  no_trade_on_data_error: true
  no_trade_on_calendar_error: true
  no_trade_on_rule_error: true
  no_trade_on_model_error: true
```

## 29. 仓位与币种管理

仓位由以下约束共同决定：

```text
allowed_position = min(
  模型风险预算,
  单标的上限,
  市场上限,
  行业上限,
  币种上限,
  流动性/容量上限,
  事件风险上限,
  账户可用资金与结算约束
)
```

可将折扣凯利作为研究指标，但生产仓位不得直接使用满凯利。

美股仓位必须同时报告：

- USD 本地市值。
- 基准币种市值。
- USD 汇率风险。
- 已结算和未结算现金。

## 30. 紧急降级

进入 `SAFE_MODE` 的条件包括：

- 市场日历或夏令时异常。
- 数据源冲突、公司行为对账失败或 ticker 身份冲突。
- A 股规则快照、交易状态或涨跌停状态缺失。
- 美股交易暂停、财报事件或拆股数据异常。
- FX 数据过期但组合含外币资产。
- 模型服务、风险服务或组合快照不可用。
- 分市场或组合回撤超过阈值。
- 生产模型效果快速恶化。

SAFE_MODE：

- 不生成新增买入建议。
- 仅显示已有持仓和风险。
- 禁止跨市场自动调仓。
- 使用最近稳定模型或简单规则作为研究对照，但不得伪装成新的正式预测。
- 经人工检查后恢复。

## 31. 推荐目录

```text
quant-agent/
├── configs/
│   ├── markets.yaml
│   ├── calendars.yaml
│   ├── risk-limits.yaml
│   ├── strategy-cn-fund.yaml
│   ├── strategy-cn-equity.yaml
│   ├── strategy-us-equity.yaml
│   └── strategy-global-portfolio.yaml
├── sql/
│   ├── schema.sql
│   └── migrations/
├── src/
│   ├── adapters/
│   │   ├── cn_market/
│   │   ├── us_market/
│   │   ├── funds/
│   │   └── fx/
│   ├── calendars/
│   ├── market_rules/
│   ├── identity/
│   ├── collectors/
│   ├── data_quality/
│   ├── corporate_actions/
│   ├── point_in_time/
│   ├── features/
│   ├── labels/
│   ├── models/
│   │   ├── cn_fund/
│   │   ├── cn_equity/
│   │   ├── us_equity/
│   │   └── global_portfolio/
│   ├── backtest/
│   │   ├── cn_execution.py
│   │   └── us_execution.py
│   ├── portfolio/
│   ├── risk/
│   ├── inference/
│   ├── evaluation/
│   ├── reporting/
│   └── api/
├── mcp/
│   ├── quant_read/
│   └── quant_admin/
├── workflows/
├── tests/
│   ├── calendars/
│   ├── point_in_time/
│   ├── corporate_actions/
│   ├── execution/
│   └── regression/
├── artifacts/
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 32. 服务划分

- `identity-service`：统一代码、ticker、CIK、交易所和公司身份。
- `calendar-rule-service`：交易日历、时段、夏令时和版本化市场规则。
- `data-service`：分市场数据采集、原始留档和质量校验。
- `corporate-action-service`：拆股、分红、并购、退市和复权因子。
- `point-in-time-service`：财务、公告和披露可用时间。
- `feature-service`：分市场特征和快照。
- `research-service`：分市场回测、成本、容量和压力测试。
- `model-service`：分市场训练、注册、校准、影子运行和推理。
- `portfolio-risk-service`：组合、FX、行业、市场和事件风险。
- `audit-service`：预测、工具调用、规则版本和人工审批留痕。
- `agent-service`：MCP 编排、报告和解释。

## 33. 上线闸门

### Gate 1：研究完成

- 假设明确。
- 代码测试通过。
- 无明显数据泄漏。
- 能够复现实验。

### Gate 2：样本外有效

- 多个滚动窗口有效。
- 扣除成本后有效。
- 优于简单基准。
- 参数相对稳定。

### Gate 3：模拟盘

建议至少观察 3～6 个月，覆盖不同市场环境。

### Gate 4：小资金实盘

- 只使用计划资金的一小部分。
- 仓位上限低于成熟阶段。
- 人工确认下单。
- 每日核对成交和模型建议。

### Gate 5：逐步扩大

只有当真实表现与回测差异可接受时才扩大资金。每次扩大后重新观察，不一次性放大。

---

# 第十阶段：建立个人算法交易事业

## 34. 来自《量化交易》的核心启示

### 34.1 从适合自己的策略开始

策略必须匹配：

- 资金规模
- 数据能力
- 编程能力
- 可投入时间
- 交易频率
- 风险承受能力
- 经纪商和市场条件

个人交易者不应直接复制需要巨大资金、低延迟基础设施或昂贵数据的机构策略。

### 34.2 简单优先

优先选择：

- 经济逻辑清楚。
- 参数较少。
- 容易验证。
- 容易执行。
- 对成本不敏感。
- 在多个时期有效。

复杂模型容易产生过拟合，也更难发现系统错误。

### 34.3 回测不是证明，而是排除工具

回测只能说明策略在给定数据和假设下发生过什么。它不能证明未来一定盈利。

一个可靠的回测需要：

- 正确的历史数据。
- 正确的时间顺序。
- 真实交易成本。
- 样本外测试。
- 多市场阶段验证。
- 对参数稳定性的检查。

### 34.4 执行系统与策略同等重要

即使策略逻辑正确，实际结果仍可能因为以下问题恶化：

- 延迟
- 滑点
- 流动性
- 订单拒绝
- 数据错误
- 网络中断
- 手工操作错误
- 基金申赎限制

因此必须建设监控、日志、降级、重试和人工确认机制。

### 34.5 风险管理决定能否生存

重点不是追求最高收益，而是避免不可恢复的损失。

应关注：

- 最大回撤
- 杠杆
- 集中度
- 相关性
- 尾部风险
- 流动性
- 连续亏损

### 34.6 从小资金开始

先证明：

1. 数据管道可靠。
2. 回测可信。
3. 模拟盘有效。
4. 小资金实盘与预期接近。
5. 执行和复盘可以长期坚持。

然后再扩大资金，而不是先扩大规模再寻找策略。

### 34.7 个人交易者的优势

个人交易者可能具有：

- 资金规模小，策略容量压力低。
- 决策链短。
- 可以交易机构不关注的小容量机会。
- 可以快速停止失效策略。
- 不需要为了排名而被迫交易。
- 可以持有现金。

但必须接受现实：

- 数据和基础设施有限。
- 无法在低延迟领域与专业机构竞争。
- 更需要选择低频、容量适中、成本可控的策略。

---

## 35. 事业化发展路径

### 阶段 1：个人投研工具

目标：

- 管理自己的基金组合。
- 每日获得结构化建议。
- 建立预测档案。
- 不自动交易。

成果：

- 数据库
- 基础策略
- 晨报
- 复盘系统

### 阶段 2：个人量化账户

目标：

- 模拟盘。
- 小资金实盘。
- 人工确认交易。
- 建立真实成本模型。

成果：

- 实盘业绩记录
- 回测与实盘偏差报告
- 稳定执行流程

### 阶段 3：多策略组合

目标：

- 长线定投
- ETF 趋势
- ETF 轮动
- 均值回归
- 防御资产配置

成果：

- 策略相关性矩阵
- 风险预算
- 组合级资金管理

### 阶段 4：产品化

可以建设：

- 量化投研看板
- 基金组合诊断
- 每日信号订阅
- 模拟组合
- 策略研究平台

但需注意所在地关于投资建议、资产管理、信号销售、自动跟单和金融数据使用的法律与牌照要求。在未完成合规评估前，不向他人承诺收益、不代客理财、不进行收益分成式资金管理。

### 阶段 5：机构化

只有在长期真实记录、合规、风控和运营能力成熟后，再考虑：

- 公司主体
- 合规咨询
- 审计
- 经纪商和托管
- 投资者适当性
- 信息披露
- 灾备
- 权限隔离
- 运营和技术团队

---

# 第十一阶段：实施计划

## 36. 分阶段实施里程碑

不以固定周数作为承诺，按验收门槛推进。

### 里程碑 1：市场抽象与建库

- 建立 `market`、`instrument`、标识符历史、市场日历和规则快照。
- 接入 A 股、中国基金/ETF、美股和 FX 的基础日线数据。
- 完成公司行为、点时点财务和退市样本设计。
- 通过时区、夏令时、节假日和身份映射测试。

### 里程碑 2：中国基金与 A 股研究基线

- 完成长线定投、ETF 基线和 A 股横截面基线。
- 完成 A 股规则感知回测器。
- 建立成本、不可成交、涨跌停和退市处理。

### 里程碑 3：美股研究基线

- 建立美股股票池和 ticker/CIK 映射。
- 完成 point-in-time 财报、公司行为和常规会话回测器。
- 完成美股 ETF 与股票横截面基线。
- 单独验证 USD 本地收益和基准币种收益。

### 里程碑 4：分市场机器学习与模型治理

- 训练 CN 和 US 候选模型。
- 执行 walk-forward、概率校准、OOD 和漂移评估。
- 建立模型注册、影子运行、发布和回滚。

### 里程碑 5：Skill + MCP + 自动化

- 实现 `quant-read-mcp` 和 `quant-admin-mcp`。
- 实现跨市场 Skill 输入校验、工具编排和报告模板。
- 完成 A 股盘前、美股盘前和跨市场组合报告。
- 完成预测留痕和自动复盘。

### 里程碑 6：模拟盘和实盘前评审

- 分市场模拟成交。
- 对账回测、预测、模拟订单和实际可成交性。
- 校准成本、延迟、FX 和经纪商约束。
- 通过安全、模型、风险、数据许可和合规评审后，才允许进入小资金人工确认阶段。

## 37. 推荐硬件

日频多市场 MVP：

- 8 核以上 CPU。
- 32 GB 内存起步。
- 500 GB 以上 SSD，并预留公司行为、财务文档索引和数据快照空间。
- Linux、自动备份和异地对象存储。
- 不强制需要 GPU。

若保存分钟行情、完整 SEC 文档、多个供应商原始快照或大规模超参数实验，存储和计算需求会显著增加，应把研究计算与生产推理解耦。

传统机器学习、横截面因子和日频回测通常可以在 CPU 上完成；只有经基准证明深度模型有增量价值时才增加 GPU。

## 38. MVP 验收标准

### 数据

- A 股、美股、基金/ETF、FX 和市场日历核心数据达到配置的完整率门槛。
- 时区、夏令时、提前收盘、公司行为和 point-in-time 测试通过。
- 历史数据、身份映射和特征快照可重建。
- 每条预测可追溯到来源、规则、日历、数据和模型版本。

### 模型

- CN 与 US 各自有明确基准和独立样本外结果。
- 扣除本地成本和 FX 后进行评价。
- 概率校准、OOD 和拒绝预测有效。
- 实验可复现。

### 系统

- A 股和美股任务可独立运行、重试、暂停和回滚。
- 异常不生成买入建议。
- 报告时间和数据截止可审计。
- 部分市场失败时，组合报告明确降级而非伪造完整结果。

### 风控

- 所有仓位经过 Risk Engine。
- 市场、行业、单票、币种和组合回撤触发限制。
- 数据、规则、日历、FX 或模型异常触发 `NO_FORECAST/NO_TRADE`。
- 真实交易能力默认不存在。

---

# 第十二阶段：第一版明确不做的事项

为降低失败概率，V1 不做：

- 高频、毫秒级、做市或盘口预测。
- 自动连接真实券商下单和自动换汇。
- 盘前、盘后或隔夜自动交易。
- OTC、极低价微盘股、期权、权证和复杂结构产品。
- 全量 A 股和全量美股无过滤预测；只运行批准股票池。
- 完全由大语言模型选择股票、基金或最终仓位。
- 每天自动重训并自动发布。
- 高杠杆和未经验证的复杂深度学习。
- 对外收费荐股、自动跟单或代客理财。

---

# 第十三阶段：最终落地建议

推荐第一版技术组合：

```text
操作系统：Rocky Linux / Ubuntu
数据库：PostgreSQL + 可选 TimescaleDB
对象存储：MinIO / S3兼容存储
语言：Python
数据处理：Polars / Pandas / PyArrow
机器学习：scikit-learn + LightGBM / XGBoost
回测：VectorBT + 分市场自建执行模拟器
模型管理：MLflow
接口：FastAPI / gRPC
MCP：quant-read-mcp + quant-admin-mcp
市场层：CN-A Adapter + US Adapter + Fund Adapter + FX Adapter
日历规则：独立 calendar-rule-service
自动化：Airflow 或 n8n + 任务队列
监控：Grafana + Prometheus + 集中日志
部署：Docker Compose，规模扩大后迁移 Kubernetes
```

推荐正式流程：

```text
市场抽象和身份建库
  ↓
分市场数据质量与 point-in-time 检查
  ↓
冻结规则、日历、股票池和研究假设
  ↓
建立 CN / US 简单基准
  ↓
分市场训练收益、排名和风险模型
  ↓
分市场样本外回测和执行模拟
  ↓
跨市场组合、FX 和风险约束
  ↓
影子运行和分市场模拟盘
  ↓
自动复盘、漂移和归因
  ↓
候选模型评审、发布或回滚
  ↓
通过独立实盘评审后才允许小资金人工确认
```

系统不追求每天必须推荐标的，而追求：

> 在身份、时间、规则、数据、模型和风险均可验证时才形成建议；没有可靠优势时拒绝交易；预测错误时控制损失，并保持跨市场结果可解释、可审计和可复现。

---


# 第十四阶段：量化 Skill 封装规格

## 39. 建议的 Skill 目录

```text
cross-market-quant-research/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── architecture.md
│   ├── market-cn-a.md
│   ├── market-us-equity.md
│   ├── funds-and-etfs.md
│   ├── calendars-timezones.md
│   ├── identifiers-corporate-actions.md
│   ├── point-in-time-policy.md
│   ├── input-output-contracts.md
│   ├── mcp-tool-contracts.md
│   ├── model-validation-policy.md
│   ├── risk-policy.md
│   ├── reporting-templates.md
│   └── review-checklist.md
└── scripts/
    ├── normalize_instrument.py
    ├── validate_input.py
    ├── validate_market_session.py
    ├── validate_data_freshness.py
    ├── validate_prediction.py
    ├── validate_currency_exposure.py
    ├── validate_risk_decision.py
    └── render_report.py
```

`SKILL.md` 保持精简，作为控制平面；市场细节放在一层 `references/` 中按需加载。Skill 不包含历史行情、模型权重、密钥、持仓或券商凭据。

建议名称：`cross-market-quant-research`，不要在名称末尾重复加入 `skill`。

## 40. Skill 的触发范围

触发场景：

- 分析中国基金、ETF、A 股或美股持仓。
- 生成 A 股盘前报告或美股常规开盘前报告。
- 生成基金定投、ETF 择时或股票横截面排名。
- 解释生产模型预测和组合 FX 风险。
- 运行或评审分市场回测。
- 检查模型是否可进入影子或生产。
- 生成日、周、月复盘。
- 检查数据、时区、日历、规则、模型漂移或风险状态。

不处理：

- 保证收益、必涨股或完美预测。
- 绕过风险引擎给出仓位。
- 使用未经记录的数据或错误时区进行预测。
- 未经审批发布模型或提交真实订单。

## 41. Skill 必填输入

| 字段 | 含义 | 规则 |
|---|---|---|
| `as_of` | 预测时点 | 必须含时区，内部转换为 UTC |
| `market` | 市场 | `CN_FUND`、`CN_A`、`US_EQUITY`、`CN_ETF`、`US_ETF` |
| `exchange_mic` | 交易所 | 个股请求尽量必填；无法唯一解析时必须追问或拒绝 |
| `instrument_type` | 资产类型 | 基金、ETF、普通股、ADR、REIT 等 |
| `session` | 市场会话 | 默认 `REGULAR` |
| `horizon_sessions` | 预测周期 | 使用本地交易时段，不使用自然日 |
| `objective` | 任务 | 定投、筛选、持仓诊断、回测、模型评审 |
| `base_currency` | 组合计价币种 | CNY、USD、KRW 等 |
| `risk_profile` | 风险政策 | 来自已批准配置 |
| `execution_mode` | 运行模式 | `RESEARCH / PAPER / LIVE`，默认 `RESEARCH` |

持仓分析还需要数量、成本、现金、账户市场权限和完整持仓快照。

## 42. Skill 标准执行流程

### 42.1 持仓与预测

```text
1. 识别市场、交易所、资产、会话、时区和预测周期
2. instrument_resolve 生成 canonical_id；有歧义则停止
3. market_calendar_get 检查是否为交易会话及开收盘时间
4. market_rules_get 获取当日规则快照
5. data_get_status 检查市场数据、财务、公司行为和 FX
6. 如涉及组合，portfolio_get_snapshot
7. model_get_production 选择该市场和任务的生产模型
8. forecast_run 获取结构化概率预测
9. risk_evaluate_proposal 获取允许仓位和风险标记
10. 校验时间戳、币种、规则、模型和概率范围
11. prediction_record 留痕
12. 生成解释；不得修改模型或风控数字
```

### 42.2 回测与模型评审

```text
1. 冻结市场、股票池、规则、日历、成本和研究假设
2. 创建 point-in-time 数据快照
3. 运行分市场简单基准
4. 发起候选模型训练
5. 执行分市场滚动样本外回测
6. 执行公司行为、退市、FX、容量和压力测试
7. 与同市场生产模型比较
8. 影子运行
9. 仅提交发布请求，不由 Skill 自动发布
```

### 42.3 失败流程

以下任一情况停止推荐：

- 市场、交易所或标的身份无法唯一解析。
- 日历、夏令时或交易规则版本不可用。
- 关键行情、公司行为、财务或 FX 数据未更新。
- 输入标的不在该模型支持股票池。
- OOD、模型分歧或不确定性超限。
- 风控引擎拒绝。
- MCP 返回无法验证的部分数据。

输出：`NO_FORECAST`、`NO_TRADE` 或 `RESEARCH_ONLY`。

# 第十五阶段：MCP 工具合同

## 43. MVP MCP 拓扑

第一版仍建议两个 MCP：

```text
quant-read-mcp
quant-admin-mcp
```

市场差异由后端 Adapter 处理。只有在合规主体、网络边界或权限完全分离时，才拆成独立的 `cn-market-mcp` 与 `us-market-mcp`。

未来若接入交易，单独建立：

```text
broker-paper-mcp
broker-live-mcp
```

交易 MCP 不与预测 MCP 混合。

## 44. `quant-read-mcp` 工具

| 工具 | 作用 | 权限 |
|---|---|---|
| `instrument_resolve` | 解析代码、ticker、交易所、CIK 和 canonical_id | 只读 |
| `market_calendar_get` | 获取会话、休市、提前收盘、时区和 DST | 只读 |
| `market_rules_get` | 获取当日板块、交易单位、价格/停牌和结算规则 | 只读 |
| `data_get_status` | 返回市场、字段组和日期的数据新鲜度 | 只读 |
| `corporate_action_get` | 返回点时点公司行为 | 只读 |
| `fundamental_get_point_in_time` | 返回预测时点已公开的财务事实 | 只读 |
| `fx_get_snapshot` | 返回批准的 FX 快照 | 只读 |
| `market_get_features` | 返回批准的特征快照，不允许任意 SQL | 只读 |
| `fund_get_profile` | 中国基金资料、费用和披露状态 | 只读 |
| `equity_get_profile` | A 股/美股身份、行业、状态和事件风险 | 只读 |
| `portfolio_get_snapshot` | 获取授权组合和现金桶 | 只读 |
| `model_get_production` | 获取市场和任务对应的生产模型 | 只读 |
| `forecast_run` | 对批准标的运行生产推理 | 计算 |
| `screen_rank` | 对批准股票池或 ETF 池排名 | 计算 |
| `cross_market_context_get` | 返回已冻结的跨市场上下文 | 只读 |
| `risk_evaluate_proposal` | 确定性风险校验 | 计算 |
| `risk_run_scenario` | 组合和 FX 压力测试 | 计算 |
| `prediction_record` | 记录输入、模型、规则、预测和建议 | 低风险写入 |
| `report_get_payload` | 返回盘前或复盘结构化数据 | 只读 |

所有工具必须返回：

- `request_id`
- `as_of_utc`
- `market_local_time`
- `data_cutoff_utc`
- `calendar_version`
- `rule_version`
- `source_versions`
- `status`、`warnings`、`error_code`

## 45. `quant-admin-mcp` 工具

保留原有管理工具，并增加市场范围：

- `dataset_create_snapshot`
- `backtest_create_job`
- `training_create_job`
- `job_get_status`
- `model_compare`
- `model_start_shadow`
- `model_request_promotion`
- `model_request_rollback`
- `policy_validate`

每个任务必须显式包含 `market_scope`、`universe_id`、`calendar_version`、`rule_version`、`base_currency` 和 `data_license_manifest_id`。

## 46. MCP Resources

```text
quant://schemas/prediction/v2
quant://schemas/risk-decision/v2
quant://markets/{market}/calendar/{date}
quant://markets/{market}/rules/{version}
quant://instruments/{canonical_id}
quant://models/{market}/{task}/{model_id}/card
quant://datasets/{snapshot_id}/manifest
quant://data-status/{market}/{date}
quant://policies/risk/current
quant://reports/{report_id}
```

## 47. 长任务处理

训练和大规模回测提交异步任务并返回 `job_id`；Agent 不维持长连接等待。长任务结果必须包含分市场指标和组合指标。

# 第十六阶段：统一预测合同

## 48. 标准预测结果 V2

```json
{
  "request_id": "pred_us_xnas_aapl_20260715_20s",
  "as_of_utc": "2026-07-15T12:30:00Z",
  "as_of_market_local": "2026-07-15T08:30:00-04:00",
  "user_display_time": "2026-07-15T21:30:00+09:00",
  "data_cutoff_utc": "2026-07-15T12:15:00Z",
  "market_code": "US_EQUITY",
  "exchange_mic": "XNAS",
  "canonical_instrument_id": "US.XNAS.EQ.AAPL",
  "instrument_type": "COMMON_STOCK",
  "session": "REGULAR",
  "horizon_sessions": 20,
  "local_currency": "USD",
  "base_currency": "CNY",
  "calendar_version": "XNYS_XNAS_2026_V2",
  "rule_version": "US_EQUITY_2026_V3",
  "model_id": "US_EQ_XS_20D_V4",
  "model_status": "PRODUCTION",
  "dataset_snapshot_id": "us_snap_20260715_preopen",
  "feature_snapshot_id": "us_feat_20260715_preopen_v7",
  "fx_snapshot_id": "usdcny_20260715_1215z",
  "prediction": {
    "expected_return_local": 0.018,
    "expected_fx_return": -0.004,
    "expected_return_base": 0.0139,
    "up_probability": 0.61,
    "return_quantiles_local": {
      "p10": -0.075,
      "p50": 0.014,
      "p90": 0.109
    },
    "drawdown_over_threshold_probability": 0.24,
    "cross_section_rank_percentile": 0.86
  },
  "uncertainty": {
    "calibration_bucket": "0.60-0.65",
    "out_of_distribution_score": 0.14,
    "ensemble_disagreement": 0.10,
    "confidence_status": "NORMAL"
  },
  "risk_flags": ["EARNINGS_WITHIN_10_SESSIONS"],
  "forecast_status": "VALID"
}
```

A 股使用相同合同，但 `market_code`、交易所、规则版本、币种和事件字段不同。

## 49. 风险决策合同

```json
{
  "forecast_request_id": "pred_us_xnas_aapl_20260715_20s",
  "portfolio_id": "portfolio_main",
  "base_currency": "CNY",
  "proposed_action": "BUY",
  "proposed_position": 0.05,
  "risk_decision": "REDUCED",
  "allowed_position": 0.025,
  "market_exposure_after": {
    "CN_A": 0.35,
    "US_EQUITY": 0.22
  },
  "currency_exposure_after": {
    "CNY": 0.63,
    "USD": 0.22
  },
  "reasons": [
    "美元未对冲暴露接近上限",
    "财报事件临近"
  ],
  "policy_version": "cross_market_risk_v4"
}
```

## 50. 拒绝预测机制

```text
身份无法解析 / 日历异常 / 规则缺失 / 数据过期
    → NO_FORECAST

模型支持但置信度不足 / 事件风险过高 / 风控拒绝
    → NO_TRADE

数据许可、股票池或执行能力不足
    → RESEARCH_ONLY
```

不得为了每日推荐而绕过拒绝机制。

# 第十七阶段：资产与市场支持矩阵

## 51. 支持程度

| 市场/品种 | MVP 支持程度 | 主要方法 | 关键限制 |
|---|---|---|---|
| 中国宽基/行业指数基金 | 高 | 估值、趋势、宏观、风险和动态定投 | 场外成交与净值发布时间 |
| 中国场内 ETF | 高 | 趋势、动量、横截面和风险模型 | 价差、溢价、流动性和板块规则 |
| A 股主板普通股 | 高/中高 | 多因子横截面、事件和风险模型 | 涨跌停、停牌、退市和不可成交 |
| 科创板/创业板 | 中高 | 独立板块规则和模型校准 | 规则、波动和上市初期样本 |
| 北交所股票 | 中 | 独立股票池和流动性模型 | 准入、流动性和价格约束 |
| 美国大中盘普通股 | 高/中高 | 基本面、价格、事件和横截面模型 | 财报跳空、身份历史、数据许可 |
| 美国高流动性 ETF | 高 | 配置、趋势、轮动和风险模型 | 盘前盘后默认不执行 |
| ADR/REIT/BDC | 中 | 类别专属特征或独立模型 | 结构、税费、母市场和分配规则 |
| 美国小盘股 | 中 | 横截面和流动性过滤 | 冲击成本和退市偏差 |
| OTC/极低价微盘股 | 不支持 V1 | 不进入生产股票池 | 数据、操纵和流动性风险 |
| 期权/高频/做市 | 不支持 | 不属于本方案 | 基础设施和风险模型完全不同 |

## 52. 基金和 ETF 的特殊处理

- 主动基金不能把最近季报持仓当作实时持仓。
- 中国基金持仓按实际披露时间进入模型。
- 美国 ETF 需要保存底层指数、费用、资产类别、分红和交易时段。
- 跨境基金/ETF 同时处理底层市场时区、估值时差、额度和溢折价。
- 相同主题的中美产品必须在组合层识别共同风险暴露。

## 53. 个股预测的正确产品形态

系统应回答：

- 当前批准股票池中哪些标的相对更强？
- 哪些标的预期收益/风险比更优？
- 如何组成行业、规模、市场和币种暴露受控的组合？
- 哪些标的应因公告、财报、交易状态、流动性或 OOD 被排除？

系统不应：

- 精确承诺明日收盘价。
- 输出必涨股票。
- 每天重仓单一股票。
- 把未经验证的跨市场相关性当作因果关系。
- 从新闻文本直接生成最终买入金额。

# 第十八阶段：预测可靠性和验收

## 54. 五类验收

### 54.1 数据验收

- A 股与美股数据均符合 point-in-time 原则。
- UTC、本地时间、夏令时、提前收盘和节假日测试通过。
- 财务数据按实际公告/接收时间，而非报告期进入模型。
- 公司行为、ticker/代码历史、退市和历史成分处理通过。
- 汇率快照和本地/基准币种收益可以对账。
- 数据快照不可变、可复现且包含许可清单。

### 54.2 模型验收

- CN 和 US 模型分别有简单基准和独立样本外验证。
- 概率经过分市场校准。
- 检查不同市场状态、行业、规模和事件分组。
- OOD、模型分歧和特征漂移可用。
- 跨市场模型必须证明其增量价值，而不能仅依赖相关性叙事。

### 54.3 策略验收

- A 股计入规则、不可成交和板块差异。
- 美股计入常规会话、提前收盘、退市、公司行为和结算。
- 两个市场均计入佣金、价差、滑点、冲击和容量。
- 跨市场组合计入 FX、休市错配和调仓延迟。
- 影子运行覆盖足够多的本地交易时段和至少一个财报/公告密集阶段。

### 54.4 系统验收

- 100% 预测带市场、时区、数据截止、日历、规则、模型和快照版本。
- 100% 建议经过风险引擎。
- 身份、日历、规则、FX 或数据异常时 fail closed。
- 分市场模型可独立暂停和回滚。
- 高风险写操作必须审批。

### 54.5 运营与许可验收

- 数据许可允许当前训练、内部使用和报告范围。
- 用户持仓按租户隔离。
- 美股数据和 SEC 抓取遵守服务访问政策。
- 经纪商、换汇、税费和账户规则在实盘前单独评审。

## 55. 发布门槛模板

候选模型必须：

1. 无已知前视、标签泄漏、身份和存活者偏差。
2. 在目标市场多个独立样本外窗口有效。
3. 扣除本地成本和 FX 后不劣于简单基准。
4. 最大回撤、尾部风险、连续亏损和容量符合政策。
5. 概率校准优于基准概率。
6. 参数和特征轻微变化不会完全崩溃。
7. 影子运行通过。
8. 数据、规则、日历、代码和模型可复现。
9. 独立评审批准。

A 股模型表现不得替代美股发布证据，反之亦然。

## 56. KPI

同时跟踪：

- Brier Score、Log Loss、校准误差。
- IC、Rank IC 和分层收益。
- 本地币种与基准币种净超额收益。
- 夏普、索提诺、最大回撤和尾部风险。
- 换手、成本、容量和不可成交比例。
- FX 贡献。
- 高置信度覆盖率和 `NO_TRADE` 避免的损失。
- 回测、影子、模拟和实盘偏差。

# 第十九阶段：安全与权限

## 57. MCP 安全基线

- 只连接受信任、自建或审查过的 MCP Server。
- 使用 OAuth 或等价短期令牌、RBAC 和租户隔离。
- 工具白名单和严格 JSON Schema。
- 日常 Agent 无模型发布、风险政策修改和真实交易权限。
- 不提供任意 SQL、Shell、Python 或文件系统工具。
- 新闻、公告和网页文本均视为不可信数据，不能成为系统指令。
- 密钥只在服务端 Secret Manager。
- 数据许可和来源标签随工具结果返回。
- 所有调用记录身份、参数摘要、结果、市场和时间。

## 58. 实盘执行隔离

```text
V1：研究 + 报告
V2：分市场模拟盘
V3：订单草稿 + 人工确认
V4：有限自动化，仅限批准策略、账户、市场、时段和额度
```

未来建议分别建立 `broker-paper-mcp` 与 `broker-live-mcp`。预测 MCP 永远不直接持有券商密钥。

跨市场实盘还必须设置：

- 分市场和分币种额度。
- 允许交易的交易所、股票池和会话白名单。
- FX 额度和换汇审批。
- 价格偏离、流动性和事件保护。
- 账户级紧急停止。
- 订单、成交、结算和持仓对账。

# 第二十阶段：未来 Skill 审核清单

## 59. 结构审核

- [ ] Skill 名称小写、简短，不含多余 `skill` 后缀。
- [ ] `SKILL.md` frontmatter 仅包含要求字段。
- [ ] description 明确 A 股、美股、基金和 ETF 的触发范围。
- [ ] `SKILL.md` 是控制平面，市场细节拆入一层 references。
- [ ] 包含 CN-A、US、时区、标识符、point-in-time 和 FX 参考文件。
- [ ] 确定性校验脚本已实际测试。
- [ ] 未打包密钥、模型大文件、持仓或历史行情。
- [ ] `agents/openai.yaml` 完整。

## 60. 行为审核

- [ ] 先解析市场和 canonical_id，再查询数据。
- [ ] 先检查日历、规则和数据，再调用预测。
- [ ] A 股与美股调用不同生产模型。
- [ ] 使用本地交易时段而非自然日。
- [ ] 正确处理 DST、提前收盘和节假日错配。
- [ ] 区分本地收益、FX 和基准币种收益。
- [ ] 区分预测和风险决策。
- [ ] 支持 `NO_FORECAST`、`NO_TRADE` 和 `RESEARCH_ONLY`。
- [ ] 不承诺收益或完美预测。
- [ ] 普通流程不能发布模型或真实下单。

## 61. MCP 审核

- [ ] 工具用途单一且 Schema 严格。
- [ ] 存在 `market_calendar_get` 和 `market_rules_get`。
- [ ] 存在标的身份历史和 point-in-time 财务接口。
- [ ] 存在 FX 快照和跨市场风险接口。
- [ ] 读写和高权限工具分离。
- [ ] OAuth、RBAC、审计和幂等完整。
- [ ] 长任务返回 `job_id`。
- [ ] 返回值包含市场、本地时间、UTC、规则和数据来源。

## 62. 模型审核

- [ ] CN 和 US 各自有研究假设和简单基准。
- [ ] 分市场 point-in-time 数据和时间切分正确。
- [ ] 公司行为、ticker/代码历史和退市处理正确。
- [ ] A 股规则感知执行器通过测试。
- [ ] 美股常规会话、提前收盘和结算处理通过测试。
- [ ] 成本、不可成交、容量和 FX 已计入。
- [ ] 概率校准、OOD 和漂移监控可用。
- [ ] 模型卡、数据卡、规则和日历版本完整。
- [ ] 影子、发布和回滚独立于 Agent。

## 63. 红线问题

出现任一项不通过：

- 声称完美预测、必涨或保证收益。
- 一个模型未经独立验证同时服务 A 股和美股。
- 使用自然日连接跨市场数据造成时间穿越。
- 忽略 DST、提前收盘、市场休市或交易规则。
- 美股财报按报告期而非公开时间进入训练。
- A 股忽略停牌、涨跌停、退市或不可成交。
- 忽略 ticker/代码变化、拆股或公司行为。
- 忽略 USD 汇率风险却给出基准币种仓位。
- 数据、规则或模型异常时仍强制推荐。
- 普通 Agent 可修改风险上限、发布模型或真实下单。
- MCP 暴露任意代码执行、数据库管理员或全部账户权限。

# 第二十一阶段：最终推荐

## 64. 最合理的落地形态

对于 A 股、美股、基金和 ETF，推荐：

```text
一个跨市场量化 Skill
+ 一个日常 quant-read-mcp
+ 一个高权限 quant-admin-mcp
+ CN-A / US / Fund / FX 市场适配器
+ 独立日历与规则服务
+ 分市场模型、回测器和模型注册
+ 跨市场组合与汇率风险引擎
+ 独立调度器、任务队列和审计系统
+ 模拟盘与人工审批
```

**不建议**第一版为 A 股和美股各做一套重复 Skill。统一 Skill 有利于保证输入、审计、拒绝预测和风险规范一致；市场差异应在 references、市场适配器、模型族和规则服务中实现。

**可以按市场拆 MCP 的条件**：

- 两个市场由不同团队或法人运营。
- 数据许可和网络边界不同。
- 权限、合规或券商基础设施必须隔离。
- 单个 MCP 工具规模已经难以治理。

否则保持两个 MCP 更简单、更安全。

最重要的原则：

> Skill 决定如何正确调用能力；MCP 决定 Agent 可以调用哪些受控能力；市场适配器处理 A 股与美股差异；量化后端计算数字；风险引擎决定是否允许行动；人类审批高风险操作。

## 65. 当前市场规则设计依据

以下资料用于核对架构中的市场时段和结算示例；生产系统必须持续读取最新版本，不能只依赖本文：

- 上海证券交易所交易时间：<https://english.sse.com.cn/start/trading/schedule/>
- 上海证券交易所股票交易机制：<https://english.sse.com.cn/start/trading/mechanism/>
- 深圳证券交易所英文站：<https://www.szse.cn/English/>
- 北京证券交易所：<https://www.bse.cn/>
- NYSE Holidays & Trading Hours：<https://www.nyse.com/trade/hours-calendars>
- SEC 关于美国多数证券 T+1 标准结算周期：<https://www.sec.gov/newsroom/press-releases/2023-29>
- SEC EDGAR：<https://www.sec.gov/edgar/search/>

这些链接用于规则服务和数据源适配器的验收，不代表允许抓取、训练或再分发其中所有数据；仍需遵守各站点访问政策和数据许可。

## 参考方法论说明

本文继续采用《量化交易：如何建立自己的算法交易事业》的核心方法：从适合自身资金和基础设施的简单策略开始；严格处理回测偏差和交易成本；将执行、风险、模拟交易和小规模验证视为策略的一部分；不因模型复杂而默认有效。

A 股、美股、多币种、Skill、MCP、MLOps、点时点数据和跨市场治理属于面向本项目的工程扩展，并非对原书内容的逐章复制。

## Skill + MCP 设计依据

Skill 使用精简入口、按需加载 references 和确定性脚本；MCP 使用最小权限、严格 Schema、读写分离和审批；数据库、调度、模型、回测和风险逻辑保留在独立后端。

## 风险声明

本文仅用于系统设计、技术研究和投资教育，不构成基金或股票推荐、收益承诺或个性化投资顾问意见。历史回测和模型预测不代表未来表现。真实交易前应核实适用交易规则、数据许可、经纪商限制、税费、换汇和法律监管要求。

---

# 第二部分：各模块工程落地实施规格

> 本部分把前述架构转换为开发团队可以直接拆任务、写代码、部署和验收的实施规格。第一阶段以“模块化单体 + 独立异步 Worker + 两个 MCP 网关”为主，不建议一开始拆成大量微服务。所有模块必须通过明确接口连接，后续可按负载和团队边界独立拆分。

## 66. 实施总原则与第一版边界

### 66.1 第一版部署单元

第一版建议只部署以下进程：

```text
quant-api
├── instrument
├── calendar-rule
├── data-catalog
├── feature
├── dataset
├── inference
├── portfolio
├── risk
├── evaluation
└── reporting

quant-worker
├── ingestion jobs
├── feature jobs
├── training jobs
├── backtest jobs
├── evaluation jobs
└── report jobs

quant-read-mcp
quant-admin-mcp
scheduler
postgresql
redis
object-storage
mlflow
monitoring
```

其中：

- `quant-api` 是模块化单体，对外提供稳定 REST/JSON 接口。
- `quant-worker` 执行采集、训练、回测等耗时任务。
- MCP 只做鉴权、参数校验、工具暴露和 API 转发，不重复实现量化逻辑。
- `scheduler` 可以先用 Airflow、Prefect 或 n8n；核心计算必须调用 Worker，而不是写在可视化节点中。
- 第一版不连接真实券商下单，只提供研究、模拟盘和人工确认后的订单草稿。

### 66.2 MVP 资产池

为避免一开始被数据和计算规模拖垮，建议：

| 市场 | 第一版资产池 |
|---|---|
| A 股 | 沪深 300、中证 500 中流动性和数据完整性合格的股票；主要宽基和行业 ETF |
| 美股 | S&P 500、Nasdaq-100 中流动性和数据完整性合格的普通股；主要宽基、行业、债券和黄金 ETF |
| 中国基金 | 用户现有持仓、候选指数基金、主动基金白名单 |
| 组合 | A 股、美股、基金、ETF、现金和汇率暴露 |

完成稳定运行后，再扩展到全市场。

### 66.3 不能作为第一版验收目标的内容

- 完美预测市场。
- 保证收益。
- 自动发布未经审批的新模型。
- 自动使用真实资金交易。
- 高频、期权、裸卖空和高杠杆。
- 使用任意 SQL、Shell 或 Python 的 MCP 工具。
- 依靠大语言模型直接生成预测数字。

---

## 67. 代码仓库与工程结构

建议采用 Monorepo：

```text
cross-market-quant/
├── apps/
│   ├── api/
│   │   ├── main.py
│   │   ├── dependencies.py
│   │   └── routers/
│   ├── worker/
│   │   ├── main.py
│   │   └── tasks/
│   ├── quant-read-mcp/
│   ├── quant-admin-mcp/
│   └── scheduler/
├── packages/
│   ├── common/
│   │   ├── ids.py
│   │   ├── time.py
│   │   ├── errors.py
│   │   ├── logging.py
│   │   └── schemas.py
│   ├── instrument/
│   ├── calendar_rule/
│   ├── data_sources/
│   ├── ingestion/
│   ├── data_quality/
│   ├── corporate_actions/
│   ├── fundamentals/
│   ├── features/
│   ├── datasets/
│   ├── labels/
│   ├── backtest/
│   ├── models/
│   ├── training/
│   ├── registry/
│   ├── inference/
│   ├── portfolio/
│   ├── risk/
│   ├── evaluation/
│   ├── reporting/
│   └── audit/
├── skills/
│   └── cross-market-quant-research/
├── sql/
│   ├── migrations/
│   ├── seeds/
│   └── views/
├── configs/
│   ├── base/
│   ├── dev/
│   ├── staging/
│   └── prod/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── replay/
│   └── e2e/
├── deploy/
│   ├── docker-compose.yml
│   ├── docker/
│   └── kubernetes/
├── scripts/
├── docs/
├── pyproject.toml
└── README.md
```

工程约束：

1. 模块之间只能通过公开接口或事件通信，不直接读取其他模块私有表。
2. 数据库迁移必须版本化。
3. 所有配置使用环境分层，不在代码中写密钥、市场时间或风险阈值。
4. 训练和推理共享同一套特征实现。
5. 任何产生预测的函数必须接收明确的 `as_of_time`，不得隐式使用系统当前时间。

---

## 68. 通用类型、ID、时间和错误合同

### 68.1 统一标的 ID

```python
from dataclasses import dataclass
from typing import Literal

Market = Literal["CN_A", "CN_FUND", "CN_ETF", "US_EQUITY", "US_ETF"]

@dataclass(frozen=True)
class InstrumentId:
    market: Market
    venue: str
    asset_type: str
    symbol: str

    def canonical(self) -> str:
        return f"{self.market}.{self.venue}.{self.asset_type}.{self.symbol}"
```

示例：

```text
CN_A.XSHG.EQ.600519
CN_A.XSHE.EQ.000001
US_EQUITY.XNAS.EQ.AAPL
US_ETF.ARCX.ETF.SPY
CN_FUND.OFF_EXCHANGE.FUND.000001
```

### 68.2 时间字段

所有核心记录至少包含：

```text
event_time_utc
market_local_time
available_at_utc
as_of_time_utc
ingested_at_utc
calendar_version
rule_version
```

- `event_time_utc`：市场事件实际发生时间。
- `available_at_utc`：数据当时真正可被模型看到的时间。
- `as_of_time_utc`：本次研究或预测的截面时间。
- `ingested_at_utc`：数据进入本系统的时间。

### 68.3 标准错误码

```text
DATA_NOT_READY
DATA_STALE
DATA_CONFLICT
UNKNOWN_INSTRUMENT
UNSUPPORTED_ASSET
UNSUPPORTED_HORIZON
MARKET_CLOSED
MODEL_NOT_AVAILABLE
MODEL_NOT_APPROVED
FEATURE_MISSING
OUT_OF_DISTRIBUTION
RISK_REJECTED
PERMISSION_DENIED
JOB_ALREADY_RUNNING
INTERNAL_ERROR
```

错误响应必须机器可读：

```json
{
  "ok": false,
  "error": {
    "code": "DATA_STALE",
    "message": "US market data is older than the required cutoff",
    "retryable": true,
    "details": {
      "required_as_of": "2026-07-14T20:00:00Z",
      "actual_as_of": "2026-07-11T20:00:00Z"
    }
  },
  "trace_id": "..."
}
```

---

## 69. 标的主数据模块 `instrument`

### 69.1 职责

- 管理统一标的身份。
- 管理代码、交易所、CIK、ISIN 等历史标识。
- 管理上市、退市、代码变更、基金合并和类别变化。
- 将用户输入的简称或代码解析为唯一 `instrument_id`。

### 69.2 输入和输出

输入：

- 数据源证券列表。
- 交易所或监管公开标识。
- 用户输入的代码、市场和名称。

输出：

- 唯一标的 ID。
- 当前有效标识。
- 指定历史日期有效的标识。
- 标的生命周期状态。

### 69.3 API

```text
POST /v1/instruments/resolve
GET  /v1/instruments/{instrument_id}
GET  /v1/instruments/{instrument_id}/identifiers
POST /v1/admin/instruments/sync
GET  /v1/admin/instruments/conflicts
```

解析请求：

```json
{
  "query": "AAPL",
  "market_hint": "US_EQUITY",
  "as_of_date": "2026-07-15"
}
```

### 69.4 核心实现

1. 先做严格代码匹配。
2. 再做历史别名匹配。
3. 最后才允许名称模糊匹配。
4. 多结果时不自动选择，返回候选让上层确认。
5. 标的合并、拆分或代码变化不得覆盖历史映射。

### 69.5 验收

- 同一时间、同一市场不能有两个有效标的占用同一代码。
- `BRK.B` 等带特殊字符代码能够稳定解析。
- 退市标的仍可用于历史回测。
- 用户仅输入“苹果”时不得在没有市场提示的情况下静默选定结果。

---

## 70. 市场日历与规则模块 `calendar-rule`

### 70.1 职责

- 维护 A 股和美股交易日历。
- 维护开盘、集合竞价、连续交易、收盘、盘前和盘后时段。
- 正确处理美股夏令时。
- 按市场、板块和生效日期返回交易规则。
- 为调度、回测、风险和报告提供同一规则源。

### 70.2 API

```text
GET /v1/markets/{market}/sessions?from=&to=
GET /v1/markets/{market}/status?at=
GET /v1/markets/{market}/next-session?after=
GET /v1/markets/{market}/rules?instrument_id=&at=
```

规则响应示例：

```json
{
  "market": "CN_A",
  "instrument_id": "CN_A.XSHG.EQ.600519",
  "effective_at": "2026-07-15T00:00:00+08:00",
  "lot_size": 100,
  "tick_size": 0.01,
  "price_limit": {
    "enabled": true,
    "upper_ratio": 0.10,
    "lower_ratio": -0.10
  },
  "same_day_sell_allowed": false,
  "rule_version": "CN_A_2026_07_V1"
}
```

### 70.3 实现要点

- 日历表保存市场本地日期，不通过简单的周一至周五推算。
- 美股使用 IANA 时区 `America/New_York`，不能写固定 UTC 偏移。
- 市场规则按 `valid_from`、`valid_to` 版本化。
- A 股不同板块、ST 状态和新股阶段通过规则属性覆盖。
- 回测必须锁定规则版本；生产预测使用当前生效版本。

### 70.4 测试

- 美国夏令时切换前后的 UTC 开盘时间。
- 美国提前收盘日。
- A 股节假日调休不应被错误识别为交易日。
- ST 状态变化前后的价格限制。
- 新上市标的特殊规则。

---

## 71. 数据源适配器模块 `data-sources`

### 71.1 接口

```python
from typing import Protocol, Iterable
from datetime import datetime

class MarketDataAdapter(Protocol):
    source_name: str

    def list_instruments(self, as_of: datetime) -> Iterable[dict]: ...

    def fetch_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
        frequency: str,
    ) -> Iterable[dict]: ...

    def fetch_corporate_actions(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> Iterable[dict]: ...

    def healthcheck(self) -> dict: ...
```

另设：

- `FundNavAdapter`
- `FundamentalAdapter`
- `FxAdapter`
- `MacroAdapter`
- `NewsMetadataAdapter`，第一版只保存结构化事件元数据，不让新闻文本直接决定交易。

### 71.2 适配器规则

- 适配器只负责读取和标准化字段，不负责计算策略特征。
- 每条记录必须携带来源、原始标识、可用时间和许可标签。
- 原始响应保存到对象存储，文件名包含 SHA-256。
- 免费接口只能作为原型或备用源，不能成为关键生产数据的唯一来源。
- 重试必须有指数退避和限流。
- 数据源失败不得自动用搜索引擎数字替代。

### 71.3 原始对象路径

```text
raw/{source}/{dataset}/{market}/{yyyy}/{mm}/{dd}/{job_id}-{sha256}.json.gz
```

### 71.4 验收

- 同一请求可重复执行且不会制造重复数据。
- 可以从原始对象重新构建 DWD 数据。
- 核心行情有主源和备源对账。
- 数据源不可用时产生明确告警和任务状态。

---

## 72. 数据采集与入库模块 `ingestion`

### 72.1 作业状态机

```text
CREATED
  ↓
RUNNING
  ├── SUCCEEDED
  ├── PARTIAL_SUCCESS
  ├── RETRY_WAIT
  └── FAILED
```

### 72.2 作业接口

```text
POST /v1/admin/ingestion/jobs
GET  /v1/admin/jobs/{job_id}
POST /v1/admin/jobs/{job_id}/retry
POST /v1/admin/ingestion/backfill
GET  /v1/data/status
```

### 72.3 幂等键

```text
source + dataset + instrument_id + event_time + frequency + revision
```

### 72.4 处理流程

```text
创建任务
  ↓
检查市场日历和需要更新的分区
  ↓
调用数据源适配器
  ↓
保存原始对象和哈希
  ↓
字段标准化
  ↓
写入 staging 表
  ↓
数据质量校验
  ↓
UPSERT 正式表
  ↓
更新数据水位线
  ↓
发布 data.updated 事件
```

### 72.5 数据水位线

每个数据集保存：

```text
latest_event_time
latest_available_at
latest_ingested_at
last_successful_job_id
record_count
quality_status
```

推理服务不得自行猜测数据是否最新，必须查询该水位线。

---

## 73. 公司行为与复权模块 `corporate-actions`

### 73.1 职责

- 处理拆股、合股、分红、送股、配股、基金分红、代码变更和退市。
- 生成前复权、后复权和不复权价格视图。
- 为回测模拟真实现金流和持仓数量变化。

### 73.2 关键原则

- 模型特征通常使用经过一致处理的复权价格。
- 执行模拟使用当时实际可交易价格，并显式模拟现金分红和数量调整。
- 公司行为的 `announced_at`、`ex_date`、`record_date`、`pay_date` 分开保存。
- 不得根据后续更正后的最终数据覆盖历史可见版本。

### 73.3 验收

- 美股拆股日前后的收益率连续。
- 现金分红能够进入回测现金账户。
- A 股除权除息后的涨跌幅计算使用正确参考价。
- 退市标的不会从历史股票池中消失。

---

## 74. 点时点财务与基金披露模块 `fundamentals`

### 74.1 数据模型

每个财务事实至少保存：

```text
instrument_id
fact_name
period_start
period_end
reported_value
currency
filing_type
filed_at
accepted_at
available_at
revision_no
source
```

基金披露保存：

```text
fund_id
report_period
published_at
holding_instrument_id
weight
shares
source
```

### 74.2 查询合同

```text
GET /v1/fundamentals/facts?instrument_id=&as_of=&fact_names=
GET /v1/funds/{fund_id}/disclosures?as_of=
```

查询必须返回 `available_at <= as_of` 的最后可见版本。

### 74.3 避免泄漏

- 中国财报按公告时间，而不是报告期末时间进入特征。
- 美股按监管申报被接收或公开的时间进入特征。
- 后续修订作为新版本保存。
- 主动基金持仓只能在实际披露后使用。

---

## 75. 数据质量模块 `data-quality`

### 75.1 检查层级

1. Schema：字段、类型、主键。
2. Domain：价格、成交量、币种、时间范围。
3. Cross-field：OHLC 关系、净值与收益一致性。
4. Cross-source：主源与备源偏差。
5. Temporal：缺口、重复、未来时间和可用时间。
6. Statistical：异常跳变、分布变化、缺失率。
7. Business：停牌、涨跌停、基金暂停申赎等状态一致性。

### 75.2 结果结构

```json
{
  "dataset": "market_bar",
  "partition": "CN_A/2026-07-15",
  "status": "BLOCK",
  "checks": [
    {
      "name": "close_cross_source_deviation",
      "severity": "CRITICAL",
      "failed_rows": 3,
      "threshold": 0.01
    }
  ]
}
```

### 75.3 处理策略

| 等级 | 行为 |
|---|---|
| INFO | 记录，不阻断 |
| WARNING | 允许研究，报告标记 |
| ERROR | 阻断相关标的推理 |
| CRITICAL | 阻断整个数据分区和买入建议 |

### 75.4 验收

- 人为注入错误 OHLC 能被发现。
- 删除最新交易日能触发 `DATA_NOT_READY`。
- 篡改可用时间为未来能触发时间穿越检查。
- 主备源偏差超阈值能阻断推理。

---

## 76. 数据库、对象存储和迁移模块

### 76.1 存储分工

| 存储 | 内容 |
|---|---|
| PostgreSQL | 主数据、日历、规则、任务、预测、决策、持仓、审计 |
| TimescaleDB 可选 | 大量分钟或日频时序 |
| 对象存储 | 原始响应、Parquet 快照、模型文件、回测报告 |
| Redis | 短期缓存、分布式锁、任务状态 |
| MLflow | 实验、指标、模型注册和模型卡链接 |

### 76.2 数据库迁移

- 使用 Alembic 或等价迁移工具。
- 每个迁移只做一个可回滚变更。
- 生产库禁止由应用启动时自动修改 Schema。
- 数据修复脚本和结构迁移分开。

### 76.3 备份

- PostgreSQL 每日全量或增量备份。
- 对象存储启用版本控制。
- 每季度执行恢复演练。
- 模型文件、配置和数据快照必须一起可恢复。

---

## 77. 特征工程与轻量特征库模块 `features`

### 77.1 第一版不引入重型在线特征平台

第一版采用：

- PostgreSQL 保存特征元数据和近期特征。
- Parquet 保存大规模历史特征快照。
- 同一 Python 函数用于训练和推理。
- `feature_set_version` 锁定特征定义。

### 77.2 特征定义合同

```python
from dataclasses import dataclass
from datetime import datetime
import pandas as pd

@dataclass(frozen=True)
class FeatureContext:
    market: str
    as_of_time: datetime
    calendar_version: str
    rule_version: str

class FeatureSet:
    name: str
    version: str
    required_datasets: tuple[str, ...]

    def compute(
        self,
        frame: pd.DataFrame,
        context: FeatureContext,
    ) -> pd.DataFrame:
        raise NotImplementedError
```

### 77.3 特征清单

公共特征：

- 1/5/10/20/60 日收益。
- 波动率、下行波动率和最大回撤。
- 均线、均线斜率和价格偏离。
- 成交量、成交额和流动性。
- 横截面排名。
- 市场状态。

A 股额外：

- 涨跌停距离。
- 停牌和复牌状态。
- ST/风险状态。
- 行业和市值暴露。
- 北向等数据仅在许可、稳定和当时可用时使用。

美股额外：

- 隔夜跳空。
- 财报日期距离。
- 市值、行业、质量和盈利因子。
- 盘前盘后数据仅在明确策略中使用。

基金额外：

- 跟踪误差。
- 基金规模和份额变化。
- 基金经理任期。
- 风格漂移。
- 估值分位和组合重复度。

### 77.4 防止训练—推理偏差

- 特征代码只有一个实现。
- 训练数据生成时记录 Git commit、依赖锁文件、数据快照和特征版本。
- 推理时验证列名、类型、顺序和缺失率。
- 新特征上线前执行历史回放对比。

### 77.5 API

```text
POST /v1/admin/features/jobs
GET  /v1/features/status?feature_set=&market=&as_of=
GET  /v1/features/snapshot/{snapshot_id}
```

---

## 78. 标签和数据集快照模块 `datasets`

### 78.1 标签构建

```text
future_return_1d
future_return_5d
future_return_20d
future_max_drawdown_5d
future_max_drawdown_20d
relative_return_rank_5d
relative_return_rank_20d
```

标签构建必须：

- 使用交易日历而不是自然日。
- 明确使用收盘到收盘、开盘到收盘或下一开盘成交假设。
- 对涨跌停、停牌和不可成交样本加标签或剔除。
- 保留用于估计成本和可成交性的字段。

### 78.2 数据集快照

```json
{
  "dataset_snapshot_id": "ds_cn_equity_202607_v1",
  "market": "CN_A",
  "task": "cross_section_5d",
  "feature_set_version": "cn_equity_v3",
  "label_version": "return_5d_v2",
  "universe_version": "cn_liquid_v4",
  "start": "2016-01-01",
  "end": "2026-06-30",
  "row_count": 1250000,
  "sha256": "..."
}
```

快照生成后不可原地修改；错误修复生成新版本。

---

## 79. 研究和回测模块 `backtest`

### 79.1 两类回测器

1. **向量化研究回测器**：快速筛选特征、规则和参数。
2. **事件驱动执行回测器**：模拟订单、不可成交、成本、公司行为和现金账户。

只有通过事件驱动回测的策略才能进入影子运行。

### 79.2 核心接口

```python
class Strategy:
    def generate_targets(self, context, data) -> list[dict]: ...

class ExecutionModel:
    def simulate_orders(self, context, targets, market_data) -> list[dict]: ...

class BacktestEngine:
    def run(self, specification: dict) -> dict: ...
```

### 79.3 回测规格

```yaml
name: cn_equity_rank_5d_v1
market: CN_A
universe_version: cn_liquid_v4
start: 2018-01-01
end: 2026-06-30
rebalance: daily
signal_time: close
execution_time: next_open
holding_period: 5_sessions
portfolio:
  top_n: 20
  max_weight: 0.05
cost_model: cn_equity_cost_v2
rule_snapshot_policy: historical
benchmark: CN_A.XSHG.INDEX.000300
```

### 79.4 A 股执行实现

- 当天收盘产生信号，默认下一交易日可执行。
- 当日买入股票默认不可当日卖出。
- 涨停买单和跌停卖单按保守规则判定无法成交或部分成交。
- 停牌不成交。
- 使用当日历史规则快照确定涨跌幅、最小价格和交易单位。
- ST、新股和退市整理状态必须显式处理。

### 79.5 美股执行实现

- 使用交易所本地时间和历史日历。
- 默认只在常规交易时段模拟，盘前盘后作为独立策略开关。
- 处理拆股、现金分红、代码变化、退市和交易暂停。
- 滑点与买卖价差按流动性分层。
- 区分本地 USD 收益和用户基准币种收益。

### 79.6 基金执行实现

- 场外基金使用未知价申赎逻辑。
- 按提交截止时间确定使用哪一日净值。
- 模拟申购费、赎回费、最低持有期限和确认时间。
- 普通场外基金不进入日内策略。

### 79.7 结果

回测报告至少输出：

- 资金曲线。
- 月度和年度收益。
- 最大回撤和回撤持续时间。
- 夏普、索提诺、卡玛。
- 换手率、成本和滑点。
- 行业、因子和币种暴露。
- 不同市场状态表现。
- 参数稳定性。
- 样本内、验证和样本外分段。
- 失败交易和不可成交统计。

---

## 80. 模型族具体实现

### 80.1 共通训练顺序

```text
规则基准
  ↓
线性模型
  ↓
树模型
  ↓
概率校准
  ↓
组合构建
  ↓
事件驱动回测
  ↓
影子运行
```

深度模型不作为第一版必要条件。

### 80.2 中国基金长线模型 `CN_FUND_LONG_A`

子模型：

1. 长期质量评分。
2. 未来 60/120 日收益回归。
3. 大回撤概率分类。
4. 风格漂移检测。
5. 与现有组合互补性评分。

第一版算法：

- 规则评分 + Ridge/Elastic Net。
- LightGBM 作为候选收益和风险模型。
- Isotonic 或 Platt 概率校准。

输出：

```text
quality_score
expected_return_60d
return_interval_60d
drawdown_probability_60d
valuation_multiplier
portfolio_multiplier
final_dca_multiplier
```

### 80.3 中国 ETF 短线模型 `CN_ETF_SHORT_C`

子模型：

- 5 日方向概率。
- 5 日预期收益。
- 5 日大回撤概率。
- 流动性和溢价过滤。

第一版：Logistic Regression + LightGBM 集成；只对高流动性 ETF 排名。

### 80.4 A 股横截面模型 `CN_EQUITY_CROSS_SECTION_B`

目标：预测未来 5/20 个交易日相对收益排名，而不是精确价格。

步骤：

1. 股票池过滤。
2. 横截面缺失处理和极值缩尾。
3. 行业和市值中性化。
4. 时间序列滚动训练。
5. 输出 Rank IC、分组收益和组合回测。
6. 风险模型限制行业、风格和单股暴露。

第一版：线性因子模型 + LightGBM Ranker/Regressor 候选模型。

### 80.5 美股横截面模型 `US_EQUITY_CROSS_SECTION_B`

与 A 股独立训练。额外处理：

- 财报事件。
- 隔夜跳空。
- 退市样本。
- 拆股和 ADR/REIT/BDC 分类。
- USD 与基准币种收益。

第一版：线性多因子 + LightGBM；分别验证大盘股和科技股集中风险。

### 80.6 美股 ETF 模型 `US_ETF_LONG_A_OR_SHORT_C`

- 长线模型关注估值、趋势、利率和资产配置。
- 短线模型关注趋势、相对强度、波动和流动性。
- 杠杆和反向 ETF 默认不进入第一版白名单。

### 80.7 市场状态模型

先使用可解释规则或聚类：

```text
BULL_LOW_VOL
BULL_HIGH_VOL
SIDEWAYS_LOW_VOL
SIDEWAYS_HIGH_VOL
BEAR
STRESS
```

市场状态不直接产生交易，只用于：

- 调整模型权重。
- 调整仓位上限。
- 分组评估表现。

---

## 81. 训练流水线模块 `training`

### 81.1 作业流程

```text
验证研究规格
  ↓
锁定数据集快照
  ↓
生成时间切分
  ↓
训练基准模型
  ↓
训练候选模型
  ↓
概率校准
  ↓
Walk-forward 验证
  ↓
事件驱动回测
  ↓
生成模型卡和数据卡
  ↓
注册为 CANDIDATE
```

### 81.2 API

```text
POST /v1/admin/training/jobs
GET  /v1/admin/jobs/{job_id}
GET  /v1/admin/training/jobs/{job_id}/artifacts
```

### 81.3 训练请求

```json
{
  "model_family": "CN_EQUITY_CROSS_SECTION_B",
  "dataset_snapshot_id": "ds_cn_equity_202607_v1",
  "training_spec_version": "cn_rank_5d_v3",
  "requested_by": "user-or-ci",
  "reason": "monthly_retrain"
}
```

### 81.4 资源限制

- 训练任务在 Worker 中运行。
- 每个任务设置 CPU、内存、超时和最大并发。
- 同一模型族同一数据快照使用分布式锁，防止重复训练。
- 训练日志、参数、随机种子和依赖版本必须留存。

### 81.5 发布前自动门槛

```text
数据快照有效
无泄漏检查通过
多窗口样本外指标通过
交易成本后表现通过
最大回撤未超限
概率校准通过
稳定性通过
模型文件安全扫描通过
模型卡完整
```

---

## 82. 模型注册与治理模块 `registry`

### 82.1 状态机

```text
DRAFT
  ↓
CANDIDATE
  ↓
SHADOW
  ↓
PRODUCTION
  ↓
RETIRED
```

允许 `PRODUCTION → RETIRED`，并支持回滚到上一生产版本。

### 82.2 模型卡必填项

- 模型用途和不适用范围。
- 市场和标的池。
- 预测周期。
- 数据集和特征版本。
- 训练、验证和测试区间。
- 算法和超参数。
- 样本外指标。
- 成本模型。
- 主要风险和失败市场状态。
- OOD 规则。
- 负责人和审批人。
- 模型文件哈希。

### 82.3 API

```text
GET  /v1/models/production?family=&market=
GET  /v1/admin/models/{model_id}
POST /v1/admin/models/{model_id}/start-shadow
POST /v1/admin/models/{model_id}/request-promotion
POST /v1/admin/models/{model_id}/approve-promotion
POST /v1/admin/models/{model_id}/rollback
```

发布和回滚必须是管理权限，日常 Agent 无权执行。

---

## 83. 在线推理模块 `inference`

### 83.1 推理流程

```text
接收请求
  ↓
解析标的和市场
  ↓
检查市场、数据水位线和特征状态
  ↓
加载该市场的生产模型
  ↓
生成或读取 as-of 特征快照
  ↓
Schema 和缺失检查
  ↓
OOD 检测
  ↓
模型推理与概率校准
  ↓
收益区间和风险模型合并
  ↓
保存原始预测
  ↓
返回预测，不在此处生成最终交易动作
```

### 83.2 请求

```json
{
  "market": "US_EQUITY",
  "instrument_ids": ["US_EQUITY.XNAS.EQ.AAPL"],
  "as_of_time": "2026-07-14T20:00:00Z",
  "horizon_sessions": 5,
  "base_currency": "CNY",
  "portfolio_id": "portfolio_main"
}
```

### 83.3 响应

```json
{
  "status": "FORECAST_AVAILABLE",
  "prediction_id": "pred_...",
  "instrument_id": "US_EQUITY.XNAS.EQ.AAPL",
  "model_id": "model_...",
  "data_snapshot_id": "snapshot_...",
  "feature_snapshot_id": "feature_...",
  "as_of_time": "2026-07-14T20:00:00Z",
  "horizon_sessions": 5,
  "positive_probability": 0.61,
  "expected_return_local": 0.018,
  "expected_return_base": 0.014,
  "return_interval_local": [-0.035, 0.072],
  "drawdown_probability": 0.24,
  "confidence": 0.58,
  "ood_status": "IN_DISTRIBUTION"
}
```

### 83.4 拒绝预测

以下情况返回 `NO_FORECAST`：

- 数据未就绪。
- 标的不在模型支持范围。
- 特征缺失超阈值。
- OOD 严重。
- 模型没有生产版本。
- 公司行为或代码映射冲突未解决。

---

## 84. 股票和基金筛选模块 `screening`

### 84.1 目的

Agent 不应逐只调用数千个标的。筛选模块负责：

- 按流动性、数据质量和白名单建立可预测池。
- 批量推理。
- 按收益风险比分组排名。
- 返回少量候选及拒绝原因。

### 84.2 API

```text
POST /v1/screens/run
GET  /v1/screens/{screen_id}
```

请求：

```json
{
  "market": "CN_A",
  "universe_version": "cn_liquid_v4",
  "strategy_family": "CN_EQUITY_CROSS_SECTION_B",
  "as_of_time": "2026-07-15T07:00:00Z",
  "horizon_sessions": 5,
  "top_k": 20
}
```

### 84.3 排名分数

```text
rank_score =
  calibrated_expected_return
  - risk_penalty
  - cost_penalty
  - liquidity_penalty
  - concentration_penalty
```

最终是否可买仍由组合和风险模块决定。

---

## 85. 组合构建模块 `portfolio`

### 85.1 第一版采用简单、可解释的约束优化

流程：

1. 读取当前持仓和现金。
2. 读取候选预测。
3. 计算预期收益、波动、相关性和币种暴露。
4. 应用单股、行业、市场、币种和换手约束。
5. 生成目标权重。
6. 将目标权重交给风险引擎复核。

### 85.2 第一版算法

- 长线：风险预算 + 最大权重 + 定投倍数。
- 横截面股票：Top-N 等权或波动率倒数权重。
- ETF 短线：固定风险单位和上限。
- 不以复杂均值—方差优化作为第一版唯一方案，避免协方差估计不稳定。

### 85.3 约束示例

```yaml
max_single_equity_weight: 0.05
max_single_etf_weight: 0.10
max_sector_weight: 0.25
max_market_weight:
  CN_A: 0.50
  US_EQUITY: 0.50
max_currency_weight:
  USD: 0.50
max_turnover_per_day: 0.15
min_cash_weight: 0.10
```

### 85.4 API

```text
POST /v1/portfolios/{portfolio_id}/proposals
GET  /v1/portfolios/{portfolio_id}/snapshot
GET  /v1/portfolios/{portfolio_id}/exposures
```

---

## 86. 确定性风险引擎 `risk`

### 86.1 职责

风险引擎是最终动作的唯一授权者。模型和 Agent 均无权绕过。

### 86.2 检查层级

1. 数据风险。
2. 模型风险。
3. 标的和流动性风险。
4. 单笔交易风险。
5. 组合集中度风险。
6. 市场和币种风险。
7. 回撤和连续亏损风险。
8. 运营和权限风险。

### 86.3 决策接口

```python
from dataclasses import dataclass

@dataclass
class RiskDecision:
    status: str                 # APPROVED / ADJUSTED / REJECTED
    approved_weight: float
    approved_notional: float
    reasons: list[str]
    policy_version: str
```

### 86.4 API

```text
POST /v1/risk/evaluate
POST /v1/risk/scenarios
GET  /v1/risk/policies/current
```

### 86.5 决策示例

```json
{
  "status": "ADJUSTED",
  "requested_weight": 0.08,
  "approved_weight": 0.04,
  "reasons": [
    "USD exposure would exceed portfolio limit",
    "Technology sector exposure is near limit"
  ],
  "policy_version": "risk_global_v6"
}
```

### 86.6 关键实现

- 风险政策使用版本化 YAML 或数据库规则。
- 生产策略配置需要双人审批或受控 CI/CD。
- 每个决策保存输入、输出、政策版本和 trace ID。
- 超过组合回撤阈值时自动进入 `SAFE_MODE`。
- 数据质量为 ERROR/CRITICAL 时自动拒绝新增风险仓位。

---

## 87. 持仓、交易账本与模拟盘模块 `ledger-paper`

### 87.1 职责

- 保存用户真实导入持仓，但不保存无必要的券商凭证。
- 保存模拟账户、现金、持仓、订单和成交。
- 计算已实现收益、未实现收益、费用和汇率归因。
- 支持回放预测到模拟订单的全过程。

### 87.2 核心表

```text
portfolio
account
cash_balance
position_lot
order_intent
paper_order
paper_fill
portfolio_valuation
pnl_attribution
```

### 87.3 订单状态

```text
DRAFT
PENDING_APPROVAL
APPROVED
SUBMITTED_TO_PAPER
PARTIALLY_FILLED
FILLED
CANCELLED
REJECTED
EXPIRED
```

第一版只允许到 `SUBMITTED_TO_PAPER`，不连接真实券商。

### 87.4 模拟成交

- A 股按下一可交易时点、交易单位、停牌和涨跌停模拟。
- 美股默认按常规交易时段模拟。
- 基金按申购截止时间和未知净值规则模拟。
- 模拟成交记录使用的价格、价差、滑点和规则版本。

### 87.5 API

```text
POST /v1/paper/accounts
POST /v1/paper/orders
GET  /v1/paper/orders/{order_id}
POST /v1/paper/market-close
GET  /v1/portfolios/{portfolio_id}/pnl
```

### 87.6 验收

- 现金和持仓复式校验平衡。
- 同一订单不会重复成交。
- 公司行为可以正确调整持仓和现金。
- 汇率变化可以单独归因。

---

## 88. 预测结果回填与复盘模块 `evaluation`

### 88.1 职责

- 按预测周期自动回填真实结果。
- 计算预测、策略、组合和执行四层表现。
- 生成每日、每周、每月复盘数据。
- 为模型漂移和发布决策提供证据。

### 88.2 回填流程

```text
查找已到期预测
  ↓
确认目标市场已经完成足够交易时段
  ↓
读取点时点实际价格和基准
  ↓
计算实际收益、最大回撤和相对收益
  ↓
写入 evaluation
  ↓
更新滚动指标
  ↓
触发漂移检查
```

### 88.3 指标分层

预测层：

- AUC、Brier、Log Loss。
- MAE、RMSE。
- IC、Rank IC。
- 概率校准。
- 区间覆盖率。

策略层：

- 扣除成本收益。
- 胜率、盈亏比和换手。
- 最大回撤。
- 不同市场状态表现。

组合层：

- 市场、行业、风格和币种归因。
- 集中度。
- 风险预算偏差。

执行层：

- 目标价与模拟成交价偏差。
- 滑点。
- 无法成交比例。
- 报告与订单延迟。

### 88.4 API

```text
POST /v1/admin/evaluations/jobs
GET  /v1/evaluations/models/{model_id}
GET  /v1/evaluations/strategies/{strategy_id}
GET  /v1/evaluations/portfolios/{portfolio_id}
```

---

## 89. 漂移监控模块 `drift`

### 89.1 数据漂移

- 特征 PSI。
- KS 或分位数变化。
- 缺失率。
- 类别比例。
- 数据源差异。

### 89.2 模型漂移

- 预测概率分布。
- 推荐数量。
- OOD 比例。
- 置信度分布。
- 特征贡献变化。

### 89.3 效果漂移

- 滚动 IC、AUC、Brier。
- 滚动超额收益。
- 滚动最大回撤。
- 高置信度组真实表现。
- 回测与影子运行偏差。

### 89.4 自动动作

| 条件 | 动作 |
|---|---|
| 轻微漂移 | 告警并观察 |
| 连续恶化 | 降低模型权重 |
| 超过停用阈值 | 切换基准模型或 `NO_TRADE` |
| 数据源异常 | 阻断相关市场推理 |
| 生产模型严重异常 | 回滚上一稳定版本并等待审批 |

自动回滚只能使用预先批准的上一版本，不能自动发布新候选模型。

---

## 90. 报告与通知模块 `reporting`

### 90.1 报告原则

- 报告中的数字只来自结构化 API。
- 大语言模型只负责解释、压缩和组织语言。
- 报告必须显示数据截止时间、模型版本、风险政策版本和预测状态。
- 不得将 `NO_FORECAST` 改写成模糊推荐。
- 推荐必须同时显示风险、仓位上限和失效条件。

### 90.2 报告数据合同

```json
{
  "report_type": "CN_PRE_MARKET",
  "report_date": "2026-07-15",
  "data_cutoff": "2026-07-14T07:00:00Z",
  "market_state": {},
  "long_term_funds": [],
  "equity_candidates": [],
  "portfolio_risks": [],
  "no_trade_reasons": [],
  "provenance": {
    "model_ids": [],
    "data_snapshot_ids": [],
    "risk_policy_version": "risk_global_v6"
  }
}
```

### 90.3 输出渠道

- Web 控制台。
- 企业微信或钉钉。
- 邮件。
- 文件归档。

通知渠道失败不影响预测落库；必须可重发且不重复生成预测。

### 90.4 模板

```text
A 股开盘前报告
美股开盘前报告
中国基金定投报告
全球组合风险报告
每日复盘
周度模型监控
月度模型评审
```

---

## 91. 自动调度和工作流模块 `scheduler`

### 91.1 调度原则

- 任务以市场事件为中心，不写死单一 UTC 时间。
- 调度器先查询日历服务，再创建实际任务。
- 每个任务有幂等键、超时、重试和依赖。
- 训练和回测是异步长任务。
- 报告只有在数据、特征、推理和风险均成功后生成。

### 91.2 A 股日流程

```text
T-1 收盘后行情更新
  ↓
基金净值和披露数据补充
  ↓
数据质量检查
  ↓
特征更新
  ↓
T 日开盘前推理
  ↓
组合和风险评估
  ↓
生成 A 股晨报
  ↓
T 日收盘后回填和复盘
```

### 91.3 美股日流程

```text
上一常规交易时段收盘
  ↓
行情、公司行为和披露更新
  ↓
数据质量检查
  ↓
特征更新
  ↓
下一常规交易时段前推理
  ↓
组合、币种和事件风险评估
  ↓
生成美股盘前报告
  ↓
收盘后回填和复盘
```

### 91.4 月度模型流程

```text
创建新数据快照
  ↓
训练候选模型
  ↓
Walk-forward + 事件回测
  ↓
自动门槛检查
  ↓
模型评审报告
  ↓
人工批准进入 SHADOW
  ↓
影子观察期
  ↓
人工批准进入 PRODUCTION 或拒绝
```

### 91.5 n8n 与量化后端的边界

n8n 可以：

- 定时触发 API。
- 查询任务状态。
- 发送通知。
- 执行人工审批节点。

n8n 不应：

- 内嵌完整训练代码。
- 直接操作生产数据库。
- 保存模型权重。
- 自行计算仓位。
- 绕过风险 API。

---

## 92. 内部 REST API 总清单

### 92.1 读取接口

```text
GET  /v1/data/status
POST /v1/instruments/resolve
GET  /v1/instruments/{id}
GET  /v1/markets/{market}/status
GET  /v1/models/production
POST /v1/forecasts/run
POST /v1/screens/run
GET  /v1/portfolios/{id}/snapshot
GET  /v1/portfolios/{id}/exposures
POST /v1/portfolios/{id}/proposals
POST /v1/risk/evaluate
GET  /v1/reports/payload
GET  /v1/evaluations/summary
```

### 92.2 管理接口

```text
POST /v1/admin/ingestion/jobs
POST /v1/admin/features/jobs
POST /v1/admin/datasets/snapshots
POST /v1/admin/backtests/jobs
POST /v1/admin/training/jobs
GET  /v1/admin/jobs/{id}
POST /v1/admin/models/{id}/start-shadow
POST /v1/admin/models/{id}/request-promotion
POST /v1/admin/models/{id}/approve-promotion
POST /v1/admin/models/{id}/rollback
POST /v1/admin/risk-policies/validate
```

### 92.3 API 通用要求

- OpenAPI Schema 自动生成并版本化。
- 请求包含 `request_id` 或由服务生成。
- 响应包含 `trace_id`。
- 写操作支持幂等键。
- 列表接口分页。
- 日期和时间使用 ISO 8601。
- 金额同时携带数值和币种。
- 不用浮点数保存正式账本金额。

---

## 93. `quant-read-mcp` 具体实现

### 93.1 定位

提供低风险、只读或受限写入的日常投研能力。MCP Server 保持无状态；状态在量化后端。

### 93.2 工具清单

```text
data_get_status
instrument_resolve
market_get_status
fund_get_profile
equity_get_profile
portfolio_get_snapshot
portfolio_get_exposures
model_get_production
forecast_run
screen_run
portfolio_create_proposal
risk_evaluate_proposal
risk_run_scenario
prediction_record
report_get_payload
evaluation_get_summary
```

其中 `prediction_record` 只能保存 Agent 已经收到的预测解释或用户确认状态，不能修改原始模型预测。

### 93.3 工具包装原则

每个 MCP 工具只调用一个明确的后端用例，不提供通用 HTTP 代理。

示意代码：

```python
async def forecast_run(args: dict, principal: Principal) -> dict:
    principal.require("forecast:run")
    request = ForecastRequest.model_validate(args)
    response = await quant_api.post(
        "/v1/forecasts/run",
        json=request.model_dump(mode="json"),
        headers={"X-Trace-Id": current_trace_id()},
    )
    return validate_forecast_response(response)
```

### 93.4 MCP Resources

可暴露只读资源：

```text
quant://markets/CN_A/status
quant://markets/US_EQUITY/status
quant://models/production
quant://risk/policy-summary
quant://portfolios/{portfolio_id}/summary
quant://reports/latest/cn-pre-market
quant://reports/latest/us-pre-market
```

Resources 只返回经过权限过滤的摘要，不返回密钥、完整训练数据或模型权重。

### 93.5 超时与失败

- 日常读取工具设置短超时。
- 推理超时返回明确错误，不让 Agent 自行估算。
- 长任务不放在 Read MCP。
- 后端不可用时返回 `DEPENDENCY_UNAVAILABLE`。

---

## 94. `quant-admin-mcp` 具体实现

### 94.1 定位

只供管理员、CI/CD 或明确授权的运维 Agent 使用。

### 94.2 工具清单

```text
ingestion_create_job
feature_create_job
dataset_create_snapshot
backtest_create_job
training_create_job
job_get_status
model_compare
model_start_shadow
model_request_promotion
model_approve_promotion
model_request_rollback
risk_policy_validate
audit_query
```

### 94.3 权限

| 工具 | 权限 |
|---|---|
| 创建数据或训练任务 | `quant-job:create` |
| 查看模型比较 | `model:read` |
| 进入影子运行 | `model:shadow` |
| 生产发布 | `model:promote` + 人工审批 |
| 回滚 | `model:rollback` + 人工审批 |
| 修改风险政策 | 不通过 MCP 直接执行，只能提交评审请求 |

### 94.4 长任务合同

创建任务立即返回：

```json
{
  "job_id": "job_...",
  "job_type": "TRAINING",
  "status": "QUEUED",
  "created_at": "2026-07-15T09:00:00Z"
}
```

Agent 使用 `job_get_status` 查询，不保持长连接等待训练完成。

### 94.5 禁止工具

```text
execute_sql
run_shell
run_python
write_file_anywhere
publish_model_without_approval
place_market_order
change_risk_limit_directly
```

---

## 95. Quant Skill 具体实现

### 95.1 目录

```text
cross-market-quant-research/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── workflow-forecast.md
│   ├── workflow-portfolio.md
│   ├── workflow-backtest-review.md
│   ├── market-cn-a.md
│   ├── market-us-equity.md
│   ├── funds-etfs.md
│   ├── data-quality.md
│   ├── model-governance.md
│   ├── risk-policy.md
│   ├── mcp-contracts.md
│   ├── response-schemas.md
│   └── audit-checklist.md
└── scripts/
    ├── validate_forecast.py
    ├── validate_risk_decision.py
    ├── validate_report_payload.py
    ├── validate_market_alignment.py
    └── validate_skill_package.py
```

### 95.2 `SKILL.md` 应只保存控制流程

建议少于 500 行，包含：

- 触发场景。
- 输入要求。
- 市场和资产路由。
- MCP 调用顺序。
- 拒绝预测规则。
- 报告生成规则。
- 禁止行为。
- 需要按场景读取的 reference 文件。

不要将完整数据库设计、模型理论和所有市场规则塞入 `SKILL.md`。

### 95.3 标准预测工作流

```text
1. 解析用户目标、市场、标的、周期和基准币种。
2. 调用 instrument_resolve。
3. 调用 data_get_status 和 market_get_status。
4. 数据未就绪则输出 NO_FORECAST。
5. 调用 model_get_production。
6. 调用 forecast_run 或 screen_run。
7. 有持仓时调用 portfolio_get_snapshot 和 exposures。
8. 调用 portfolio_create_proposal。
9. 必须调用 risk_evaluate_proposal。
10. 使用验证脚本检查返回结构。
11. 根据结构化数字生成解释。
12. 显示数据截止、模型、风险和不确定性。
```

### 95.4 Skill 禁止行为

- 不得根据常识补全缺失行情。
- 不得把新闻情绪直接转成仓位。
- 不得改变 MCP 返回的概率或金额。
- 不得在风险拒绝后继续推荐买入。
- 不得宣称完美预测、稳赢或保证收益。
- 不得调用 Admin MCP 发布模型，除非用户明确执行管理流程且权限足够。
- 不得连接真实券商下单。

### 95.5 验证脚本

`validate_forecast.py` 检查：

- 时间、市场和标的一致。
- `positive_probability` 在 0 到 1。
- 预测区间顺序正确。
- 数据和模型版本存在。
- 拒绝预测时不得含买入动作。

`validate_risk_decision.py` 检查：

- `REJECTED` 时批准仓位必须为零。
- 批准仓位不得高于请求仓位。
- 决策必须包含风险政策版本。
- 建议金额与币种完整。

### 95.6 Skill 输出模板

每个推荐至少包含：

```text
标的与市场
数据截止时间
预测周期
上涨概率
预期收益和区间
回撤风险
模型置信度
组合影响
风险引擎决定
建议金额或仓位
持有和退出条件
失效条件
NO_FORECAST / NO_TRADE 原因
```

---

## 96. 身份、权限与安全模块 `security`

### 96.1 身份主体

- 人类用户。
- Quant Skill Agent。
- Read MCP。
- Admin MCP。
- Scheduler。
- Worker。
- CI/CD。

每个主体使用独立身份和最小权限。

### 96.2 认证与授权

- 远程 MCP 使用 OAuth 2.1 或组织批准的等价机制。
- 内部 API 使用短期服务令牌或 mTLS。
- 角色：`viewer`、`researcher`、`operator`、`model_approver`、`risk_approver`、`admin`。
- 高风险操作要求显式审批和审计。

### 96.3 密钥

- 使用 Secrets Manager、Vault 或部署平台 Secret。
- 不写入 Skill、代码仓库、配置样例、日志或提示词。
- 数据源密钥按市场和环境分开。
- 定期轮换。

### 96.4 提示注入防护

- 外部新闻、公告和网页文本视为不可信数据。
- 文本不得改变工具权限、风险政策或 Skill 流程。
- MCP 工具调用只接受结构化参数。
- 对所有写操作进行服务器端授权，不依赖 Agent 自律。

### 96.5 数据保护

- 用户持仓属于敏感数据。
- 报告渠道按用户隔离。
- 日志中对账户号和身份字段脱敏。
- 非必要不将完整持仓发送给大语言模型；优先发送聚合暴露。

---

## 97. 审计模块 `audit`

### 97.1 审计对象

- 数据采集和修订。
- 特征和数据集快照。
- 训练和回测。
- 模型状态变化。
- 每次预测。
- 风险决策。
- Agent 工具调用。
- 用户确认。
- 模拟订单和成交。

### 97.2 审计字段

```text
actor_id
actor_type
action
resource_type
resource_id
before_hash
after_hash
request_id
trace_id
created_at
ip_or_service_identity
approval_id
```

关键审计日志使用追加写，不允许应用普通账号删除。

---

## 98. 可观测性模块 `observability`

### 98.1 指标

数据：

```text
data_lag_seconds
ingestion_success_rate
data_quality_failure_count
source_deviation_count
```

模型：

```text
inference_latency_seconds
forecast_count
no_forecast_count
ood_ratio
model_load_failure_count
rolling_ic
rolling_brier
```

系统：

```text
api_error_rate
worker_queue_depth
job_duration
mcp_tool_error_rate
report_delivery_failure_count
```

风险：

```text
risk_rejection_count
portfolio_drawdown
currency_exposure
concentration_limit_breach
```

### 98.2 日志

使用结构化 JSON 日志：

```json
{
  "level": "INFO",
  "service": "inference",
  "trace_id": "...",
  "request_id": "...",
  "model_id": "...",
  "instrument_id": "...",
  "message": "forecast completed"
}
```

不得记录 API 密钥、完整账户号或未经脱敏的敏感字段。

### 98.3 告警

- 市场开盘前数据未就绪。
- 关键数据源失败。
- DQ 阻断。
- 生产模型加载失败。
- OOD 比例异常。
- 组合回撤超阈值。
- 报告发送失败。
- 审计日志写入失败。

---

## 99. 部署实施

### 99.1 开发环境

Docker Compose 运行：

```text
postgres
redis
minio
mlflow
quant-api
quant-worker
quant-read-mcp
quant-admin-mcp
scheduler
grafana
prometheus
```

开发环境使用匿名化样例持仓和有限历史数据。

### 99.2 测试环境

- 使用独立数据库和对象存储。
- 每日从生产脱敏快照构建测试数据。
- 发布前执行历史回放。
- MCP 和 Skill 连接测试环境，不接触生产凭证。

### 99.3 生产环境

小规模第一版可以继续使用容器编排平台或高可用 Docker 主机；满足以下条件后再迁移 Kubernetes：

- Worker 需要水平扩展。
- 多团队独立发布。
- 市场数据负载显著增加。
- 需要多可用区容灾。

### 99.4 网络

```text
Internet/Data Vendors
        ↓
Egress-controlled Collectors
        ↓
Private Quant Network
        ├── API/Worker
        ├── DB/Object Storage
        └── MCP Gateway
        ↓
Authorized Agent Host
```

数据库不直接暴露公网。

### 99.5 发布

- 应用镜像不可变并带 Git SHA。
- 数据库迁移先于应用发布。
- 使用蓝绿或滚动发布。
- 生产模型与应用发布解耦。
- 出现问题可分别回滚代码、模型和风险配置。

---

## 100. 测试体系

### 100.1 单元测试

- ID 解析。
- 时间和时区转换。
- 交易日历。
- 复权计算。
- 特征计算。
- 标签计算。
- 风险规则。
- 收益和汇率换算。

### 100.2 合同测试

- 数据源适配器输出 Schema。
- API 请求响应。
- MCP 工具 Schema。
- Skill 验证脚本。
- 模型输入输出列。

### 100.3 集成测试

- 采集到入库。
- 入库到特征。
- 特征到推理。
- 推理到组合和风控。
- 风控到报告。
- 预测到结果回填。

### 100.4 历史回放测试

选择已知日期，以当时可见数据运行完整系统，验证：

- 不读取未来数据。
- 交易日和规则正确。
- 预测可复现。
- 报告数字与数据库一致。

### 100.5 故障注入

- 数据源超时。
- 数据缺失。
- 主备源冲突。
- Redis 不可用。
- 模型文件损坏。
- 数据库只读。
- MCP 超时。
- 报告渠道失败。

目标不是所有任务继续运行，而是系统正确降级且不产生不安全建议。

### 100.6 Golden Dataset

为 A 股、美股、基金各建立小型固定数据集，覆盖：

- 正常交易日。
- 停牌和涨跌停。
- 夏令时和提前收盘。
- 拆股和分红。
- 基金申赎和净值延迟。
- 退市或代码变化。

所有关键版本必须在 Golden Dataset 上通过回归测试。

---

## 101. A 股市场适配器实施清单

### 101.1 数据

- 沪深北标的列表和历史状态。
- 日线和必要的分钟线。
- 停复牌。
- ST 和风险警示状态。
- 公司行为。
- 实际公告时间的财务数据。
- 指数成分历史。
- 行业分类历史。

### 101.2 规则

- 交易日历和竞价时段。
- 板块和状态对应的交易单位、价格单位和价格限制。
- 当日买入后的卖出限制。
- 停牌、涨跌停和不可成交。
- 新股和退市阶段的特殊处理。

### 101.3 模型

- A 股股票横截面模型。
- A 股 ETF 短线模型。
- 中国基金长线模型。
- A 股市场状态模型。

### 101.4 验收场景

- 收盘信号在下一交易日执行。
- 涨停时不假设买入成功。
- 跌停时不假设卖出成功。
- 停牌期间持仓净值和风险仍可计算。
- 财报公告前不出现财务特征。

---

## 102. 美股市场适配器实施清单

### 102.1 数据

- NYSE、Nasdaq、NYSE Arca 等标的和历史状态。
- 普通股、ETF、ADR、REIT、BDC 分类。
- 日线、公司行为和退市数据。
- 实际申报时间的基本面。
- 历史指数成分或无存活者偏差股票池。
- USD 汇率数据。

### 102.2 规则

- `America/New_York` 时区。
- 常规时段、盘前和盘后标记。
- 节假日和提前收盘。
- 交易暂停。
- 订单和结算规则版本。

### 102.3 模型

- 美股横截面模型。
- 美股 ETF 长短线模型。
- 美股市场状态模型。
- 汇率影响模型或情景层。

### 102.4 验收场景

- 夏令时切换时调度正确。
- 拆股前后收益连续。
- 退市股票保留在历史股票池。
- 财报发布前不使用新财务事实。
- USD 本地收益和用户本位币收益分开。

---

## 103. 基金与 ETF 适配器实施清单

### 103.1 场外基金

- 净值和累计净值。
- 申购、赎回和暂停状态。
- 费用和最低持有期限。
- 基金经理、规模和披露。
- 基准指数和风格暴露。
- 未知价申赎模拟。

### 103.2 场内 ETF

- 行情、成交额和买卖价差。
- 规模、份额、折溢价和跟踪误差。
- 底层指数和资产类别。
- 跨境 ETF 的时区和净值错位风险。

### 103.3 基金评分输出

```text
长期质量
估值
回撤风险
风格稳定性
经理稳定性
规模和流动性
与现有组合相关性
定投倍数
替换优先级
```

### 103.4 验收

- 普通场外基金不被推荐为日内交易品种。
- 披露滞后不会被当成实时持仓。
- 高溢价 ETF 能触发风险警告。
- 基金之间的重复暴露可被识别。

---

## 104. 跨市场组合与汇率模块实施清单

### 104.1 统一估值

每个持仓同时保存：

```text
quantity
local_price
local_currency
local_market_value
fx_rate_to_base
base_market_value
```

### 104.2 收益归因

```text
标的本地价格收益
现金分红收益
汇率收益
交易成本
税费或其他费用
总本位币收益
```

### 104.3 风险

- A 股与美股总市场暴露。
- 行业和共同因子暴露。
- USD、CNY、KRW 等币种暴露。
- 交易时段错位造成的隔夜风险。
- 中国科技与美国科技的跨市场集中风险。

### 104.4 情景测试

```text
A 股指数 -10%
美股指数 -10%
USD 对基准币种 -5%
全球科技板块 -15%
利率上升冲击
A 股与美股同时下跌
```

输出组合损失、最大风险贡献标的和建议降仓顺序。

---

## 105. 数据许可和供应商替换机制

### 105.1 许可元数据

每个数据集保存：

```text
source
license_id
allowed_use
redistribution_allowed
retention_policy
expires_at
```

### 105.2 替换机制

- 策略代码只依赖标准化数据合同。
- 数据源适配器可替换。
- 主源和备源的字段映射通过配置维护。
- 切换前执行历史重叠期对账。
- 许可不允许再分发的数据不得通过 MCP 原样暴露。

---

## 106. 三条端到端流程

### 106.1 “分析我的现有基金并给出定投建议”

```text
用户输入基金代码、持仓和基准币种
  ↓
Skill 调用 instrument_resolve
  ↓
检查基金净值、披露和模型数据状态
  ↓
调用基金质量、收益和回撤模型
  ↓
读取现有组合相关性和风险贡献
  ↓
生成定投方案
  ↓
风险引擎限制金额和集中度
  ↓
Skill 输出定投倍数、区间、风险和替换建议
```

### 106.2 “今天 A 股有哪些候选”

```text
检查 A 股市场会话和数据水位线
  ↓
运行 A 股白名单筛选
  ↓
批量横截面预测
  ↓
应用流动性、涨跌停和状态过滤
  ↓
构建 Top-N 目标权重
  ↓
读取当前持仓并做组合风险评估
  ↓
返回 APPROVED / ADJUSTED / NO_TRADE
```

### 106.3 “美股开盘前给我建议”

```text
检查美股交易日、夏令时和数据截止
  ↓
读取公司行为和最新公开披露
  ↓
运行美股股票与 ETF 模型
  ↓
加入 USD 汇率和当前组合币种暴露
  ↓
风险引擎检查财报、跳空、行业和币种风险
  ↓
生成美股盘前报告
```

---

## 107. 分阶段开发计划

### 阶段 0：工程底座，2 周

交付：

- Monorepo。
- CI、代码质量和测试框架。
- PostgreSQL、Redis、对象存储和 MLflow。
- API/Worker 基础框架。
- 统一 ID、时间、错误和审计。

### 阶段 1：主数据、日历和采集，3 周

交付：

- 标的主数据。
- A 股和美股日历规则。
- 日线、基金净值、汇率和公司行为采集。
- 原始对象存储。
- 数据质量和水位线。

### 阶段 2：特征、数据集和回测，3 周

交付：

- 公共和分市场特征。
- 点时点数据集。
- 向量化和事件驱动回测器。
- 成本模型。
- Golden Dataset。

### 阶段 3：第一批模型，4 周

交付：

- 中国基金长线模型。
- 中国 ETF 短线模型。
- A 股横截面基准和候选模型。
- 美股横截面基准和候选模型。
- 概率校准和模型卡。

### 阶段 4：组合、风险和复盘，3 周

交付：

- 组合快照和目标权重。
- 风险引擎。
- 模拟账户。
- 结果回填、漂移和报告。

### 阶段 5：MCP、Skill 和自动化，2 周

交付：

- Read/Admin MCP。
- Quant Skill。
- n8n/Airflow 工作流。
- 日报、周报和月报。
- 完整权限和审计。

### 阶段 6：影子运行，至少 3 个月

- 不使用真实资金自动交易。
- 记录每日预测和模拟成交。
- 修正数据、执行和报告偏差。
- 候选模型达到门槛后再讨论小资金人工确认实盘。

---

## 108. 开发任务拆分模板

每个开发任务必须包含：

```text
任务 ID
模块
用户故事
输入输出合同
数据库变化
权限
失败和降级行为
单元测试
集成测试
可观测性
验收样例
文档更新
```

示例：

```text
ID: CAL-004
模块: calendar-rule
用户故事: 系统能够正确计算 2026 年美股每个交易日的常规时段 UTC 时间
输入: market=US_EQUITY, date range
输出: session list with local/UTC timestamps
失败: 日历版本缺失时返回 CALENDAR_NOT_AVAILABLE
测试: 夏令时切换、提前收盘、节假日
验收: 与批准的基准日历样本逐日一致
```

---

## 109. 模块 Definition of Done

一个模块只有同时满足以下条件才算完成：

1. 输入输出 Schema 明确。
2. 数据库迁移通过。
3. 单元测试通过。
4. 合同测试通过。
5. 至少一个端到端场景通过。
6. 错误码和降级行为明确。
7. 指标、日志和告警存在。
8. 权限经过检查。
9. 文档和运行手册完成。
10. 能在测试环境重复部署。
11. 关键结果可通过 trace ID 审计。
12. 不产生未经风险引擎批准的交易建议。

---

## 110. 性能和容量目标

第一版建议目标：

| 能力 | 目标 |
|---|---|
| 日线更新 | 在目标市场收盘后 60 分钟内完成核心数据 |
| 单标的推理 | P95 小于 2 秒，不含首次模型冷启动 |
| 500 标的批量筛选 | 10 分钟内完成特征、推理和排名 |
| 晨报 | 目标市场开盘前完成并留出人工检查时间 |
| 数据质量阻断 | 发现关键异常后 1 分钟内阻止后续建议 |
| 模型回滚 | 批准后 10 分钟内恢复上一生产版本 |
| 审计 | 每次工具调用和风险决定均可查询 |

具体目标应根据数据供应商和部署资源压测后调整。

---

## 111. 生产运行手册最低内容

必须建立：

- 数据源故障处理。
- 数据冲突处理。
- 日历和规则更新。
- 模型服务故障。
- 模型回滚。
- 风险超限和 SAFE_MODE。
- 报告未发送。
- 数据库恢复。
- 对象存储恢复。
- 密钥泄露和轮换。
- MCP 权限异常。
- 用户持仓导入错误。

每个手册包含负责人、触发条件、操作步骤、验证方式和升级路径。

---

## 112. 最终工程验收矩阵

### 112.1 数据

- A 股、美股、基金和汇率数据可更新。
- point-in-time 可用时间正确。
- 公司行为和退市样本正确。
- 数据水位线和质量阻断有效。

### 112.2 模型

- 各市场独立模型。
- 时间序列样本外验证。
- 概率校准。
- 事件驱动回测计入成本和不可成交。
- 支持 OOD 和 `NO_FORECAST`。

### 112.3 组合和风险

- 当前持仓、行业、市场和币种暴露正确。
- 风险引擎可调整或拒绝建议。
- 回撤超限进入 SAFE_MODE。
- Agent 无法绕过。

### 112.4 Skill 和 MCP

- Skill 能按市场路由。
- Read/Admin MCP 权限隔离。
- 无任意 SQL、Shell、Python 或直接发布工具。
- 所有工具参数和返回经过 Schema 校验。
- 高风险操作有人工审批。

### 112.5 运营

- 自动日流程成功。
- 失败可重试且幂等。
- 监控和告警可用。
- 报告数字可追溯。
- 备份恢复和模型回滚演练通过。

---

## 113. 实际开工顺序

推荐严格按照以下顺序实施：

```text
统一 ID、时间和审计
  ↓
标的主数据、日历和规则
  ↓
A 股、美股、基金和汇率数据采集
  ↓
数据质量和 point-in-time 数据
  ↓
特征和数据集快照
  ↓
简单基准和事件驱动回测
  ↓
分市场模型
  ↓
推理、组合和风险引擎
  ↓
模拟账户和复盘
  ↓
Read/Admin MCP
  ↓
Quant Skill
  ↓
自动调度、监控和影子运行
```

不要先写 Skill 再补后端。Skill 是最后的控制和交互层；其可靠性取决于下方数据、模型、风险和审计模块是否已经建立。

---

## 114. V4 方案合理性结论

按本实施规格建设后，系统能够支持：

- 中国基金和 ETF 的长线评分、定投建议和中短线概率预测。
- A 股的横截面选股、收益风险排名和风险受限组合。
- 美股股票和 ETF 的独立模型、时区、公司行为和汇率处理。
- A 股、美股、基金和现金的统一组合风险管理。
- 自动建库、更新、特征计算、训练、模型治理、推理和复盘。
- 通过 Skill + MCP 让 Agent 安全、可审计地调用量化能力。

但即使全部模块通过验收，也只能提升预测的统计可靠性和系统纪律，不能保证完美预测或持续盈利。系统的正确成功标准是：

> 数据不完整时不预测；模型没有统计优势时不交易；建议必须经过组合和风险约束；所有结果可以复现、审计、复盘和回滚。

---

## V4 风险声明

本文为工程设计和研究实施规格，不构成基金或股票推荐、收益承诺或个性化投资顾问意见。历史回测、模拟盘和模型概率均不代表未来结果。真实交易前必须完成数据许可、市场规则、经纪商能力、账户权限、税费、换汇和所在地监管要求的独立核查。
