# cross-market-quant

跨市场量化投研与 Agent 平台。覆盖中国基金/ETF、A 股、美股 / 美股 ETF，四类资产 + 跨市场组合。

> 本项目按 [`readme/A股美股基金量化Agent_Skill+MCP模块实施规格_V4.md`](readme/A股美股基金量化Agent_Skill+MCP模块实施规格_V4.md) 分阶段实施。

## 顶层约束（贯穿所有模块）

- **时间**：所有时序记录携带 `event_time_utc / market_local_time / available_at_utc / as_of_time_utc / ingested_at_utc / calendar_version / rule_version`。
- **身份**：唯一键为 `InstrumentId(market, venue, asset_type, symbol).canonical()`；`ticker` 不作数据库唯一键。
- **Point-in-time**：训练/推理查询 `available_at_utc <= as_of_time_utc`；财报按公告/SEC 接收时间进入。
- **一份特征代码**：训练与推理共用同一 `FeatureSet.compute()`。
- **Fail-closed**：任一环节异常 → `NO_FORECAST / NO_TRADE`。风控引擎是最终授权者。
- **禁令**：MCP 不暴露任意 SQL/Shell/Python；Skill 不修改概率/仓位数字；LLM 不承诺收益。

## 目录

```
cross-market-quant/
├── apps/
│   ├── api/               # FastAPI 单体（模块化）
│   ├── worker/            # RQ Worker（长任务）
│   ├── quant-read-mcp/    # 只读能力 MCP
│   ├── quant-admin-mcp/   # 管理 MCP（发布/回滚需审批）
│   └── scheduler/         # 事件驱动调度
├── packages/              # 领域模块，通过公开接口互相调用
│   ├── common/            # 统一 ID / 时间 / 错误 / 日志 / Schema
│   ├── audit/             # 追加写审计
│   ├── instrument/        ├── calendar_rule/     ├── data_sources/
│   ├── ingestion/         ├── data_quality/      ├── corporate_actions/
│   ├── fundamentals/      ├── features/          ├── datasets/
│   ├── labels/            ├── backtest/          ├── models/
│   ├── training/          ├── registry/          ├── inference/
│   ├── portfolio/         ├── risk/              ├── evaluation/
│   ├── reporting/         └── ...
├── skills/cross-market-quant-research/
├── sql/{migrations,seeds,views}
├── configs/{base,dev,staging,prod}
├── tests/{unit,contract,integration,replay,e2e}
├── deploy/docker-compose.yml
└── pyproject.toml
```

## 快速开始（开发）

> **生产部署 / 迁移 / 回滚 / 监控 / 告警**：见 [`deploy/README.md`](deploy/README.md) 与 [`deploy/.env.example`](deploy/.env.example)。

```powershell
# 依赖（推荐 uv）
python -m pip install uv
uv venv
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev,ml,backtest,data-sources]"

# 起本地依赖栈
docker compose -f deploy/docker-compose.yml up -d

# 数据库迁移
alembic upgrade head

# API
uvicorn apps.api.main:app --reload --port 8000

# 健康检查
curl http://localhost:8000/v1/health
```

## V1 明确不做

真实券商下单、自动换汇、盘前盘后自动交易、期权/权证、OTC/微盘、每日自动重训自动发布、通用 SQL/Shell/Python 的 MCP 工具、LLM 直接生成预测数字、对外收费荐股/代客理财。

## 风险声明

本项目仅用于系统设计与投研研究，不构成投资建议、收益承诺或代客理财。
历史回测、模拟盘与模型概率均不代表未来结果。真实交易前必须完成数据许可、市场规则、经纪商能力、账户权限、税费、换汇与所在地监管要求的独立核查。
