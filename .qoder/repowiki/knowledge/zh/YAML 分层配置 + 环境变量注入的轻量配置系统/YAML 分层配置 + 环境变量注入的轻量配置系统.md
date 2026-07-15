---
kind: configuration_system
name: YAML 分层配置 + 环境变量注入的轻量配置系统
category: configuration_system
scope:
    - '**'
source_files:
    - configs/base.yaml
    - configs/dev.yaml
    - .env.example
    - apps/api/main.py
    - apps/worker/main.py
    - packages/common/log.py
    - sql/migrations/env.py
    - pyproject.toml
---

## 1. 使用的系统与工具
- 配置文件格式：YAML（`configs/base.yaml`、`configs/dev.yaml`）
- 运行时变量：`.env.example` 作为模板，实际值通过 `os.getenv()` 在应用启动时读取
- 依赖声明：`pyproject.toml` 中引入 `pydantic-settings>=2.3`，但当前代码尚未使用其 Settings 模型加载 YAML
- 日志配置：通过 `packages/common/log.py` 用 structlog 初始化，级别由 `LOG_LEVEL` 环境变量控制

## 2. 关键文件与位置
- `configs/base.yaml` — 全局默认配置（app、markets、point_in_time、feature_engineering、risk_engine、model_registry、mcp、llm 等）
- `configs/dev.yaml` — 开发环境覆盖层，通过 `extends: base.yaml` 继承并覆写；所有外部连接信息以 `${VAR}` 占位符引用环境变量
- `.env.example` — 完整的环境变量清单（Postgres、Redis、S3/MinIO、MLflow、数据源 Token、JWT 等）
- `apps/api/main.py` / `apps/worker/main.py` — 应用入口，目前仅从 `os.getenv` 读取少量键（`APP_ENV`、`REDIS_URL`、`LOG_LEVEL`），未加载 YAML
- `packages/common/log.py` — 唯一消费配置的地方（`LOG_LEVEL`）
- `sql/migrations/env.py` — Alembic 迁移通过 `DATABASE_URL` 环境变量构造引擎
- `pyproject.toml` — 声明 `pydantic-settings` 依赖，但未见对应 Settings 类实现

## 3. 架构与设计决策
- **分层 YAML 继承**：`dev.yaml` 通过 `extends: base.yaml` 机制复用基线配置，按环境叠加差异。`base.yaml` 定义跨市场规则（CN/US 交易所、结算周期、涨跌停、风险层级、模型状态机等）。
- **环境变量注入**：`dev.yaml` 中的数据库、缓存、对象存储、MLflow 等敏感或环境相关项统一以 `${ENV_VAR}` 形式注入，避免硬编码。
- **最小化运行时加载**：当前各 app 入口并未显式解析 YAML，而是直接读 `os.getenv`，说明 YAML 配置尚未被框架层消费，仍处于“文档化基线”阶段。
- **日志即配置点**：唯一已落地的配置消费点是 logging，通过 `configure_logging()` 读取 `LOG_LEVEL`。

## 4. 开发者应遵循的规则
1. **新增配置项优先写入 `configs/base.yaml`**，并在 `configs/dev.yaml` 中以 `${VAR}` 形式提供可覆盖值。
2. **敏感/环境相关参数一律走环境变量**（参考 `.env.example` 命名约定），不要在代码中硬编码 URL、密钥、bucket 名。
3. **如需在 Python 中读取 YAML 配置**，建议基于已声明的 `pydantic-settings` 构建 Settings 模型，将 YAML 解析为 Pydantic model，再在各包内通过单例或依赖注入获取，而非散落 `os.getenv` 调用。
4. **保持分层语义一致**：`base.yaml` 放不可变默认值，`dev.yaml` 只放环境差异；生产环境应新增 `prod.yaml` 并通过相同 `extends` 机制组合。
5. **日志级别通过 `LOG_LEVEL` 控制**，不要修改 `packages/common/log.py` 中的默认值，除非有全局策略变更。
6. **Alembic 迁移**继续通过 `DATABASE_URL` 环境变量驱动，不要在此处引入新的配置来源。

## 5. 现状评估
该仓库已具备完整的 YAML 分层配置结构与环境变量规范，但 Python 侧尚未实现统一的配置加载器（YAML → Settings 模型）。当前处于“配置即文档 + 环境变量直读”的过渡态，后续可按 pydantic-settings 模式补齐集中式加载逻辑，使 `configs/*.yaml` 真正生效于运行时。