---
kind: configuration_system
name: 配置系统：YAML 分层 + 环境变量注入的轻量运行时配置
category: configuration_system
scope:
    - '**'
source_files:
    - configs/base.yaml
    - configs/dev.yaml
    - deploy/.env.example
    - deploy/docker-compose.yml
    - pyproject.toml
    - apps/api/main.py
    - apps/worker/main.py
    - apps/quant-read-mcp/db_backends.py
    - packages/common/log.py
    - packages/drift/__init__.py
---

## 1. 采用的方案与工具
- 配置文件格式：YAML（`configs/base.yaml`、`configs/dev.yaml`），通过 `extends` 实现环境覆盖。
- 运行时参数：环境变量（`.env.example`、`deploy/docker-compose.yml`）注入到 YAML 占位符 `${VAR}`，以及各进程直接 `os.getenv` 读取。
- 依赖声明与构建：`pyproject.toml`（Hatchling），通过 optional-dependencies 区分 ml / backtest / data-sources / dev 等安装集。
- 数据库迁移：Alembic（`alembic.ini` + `sql/migrations/`），迁移脚本由时间戳前缀命名。
- 容器编排：`deploy/docker-compose.yml` 提供 Postgres、Redis、MinIO、MLflow、Prometheus、Grafana 本地一键启动。

## 2. 关键文件与位置
- 应用级配置
  - `configs/base.yaml`：全局默认（app、markets、point_in_time、feature_engineering、risk_engine、model_registry、mcp、llm）
  - `configs/dev.yaml`：开发覆盖层，使用 `extends: base.yaml` 并通过 `${DATABASE_URL}` 等占位符注入环境变量
- 环境变量模板与编排
  - `deploy/.env.example`：所有外部化变量清单（DB、Redis、S3/MinIO、MLflow、数据源超时、端口等）
  - `deploy/docker-compose.yml`：服务定义 + 环境变量透传 + 健康检查
- 包与工具链配置
  - `pyproject.toml`：项目元信息、依赖、Ruff/Black/Mypy/pytest/coverage 工具链设置
- 运行期直接读 env 的入口
  - `apps/api/main.py`：`APP_ENV`、日志初始化
  - `apps/worker/main.py`：`REDIS_URL` 驱动 RQ Worker
  - `apps/quant-read-mcp/server.py`、`db_backends.py`：按 `DATABASE_URL` 条件启用 DB 后端
  - `scripts/*.py`：入库/训练/回测脚本统一从 `DATABASE_URL` 取连接串
- 文档化约定
  - `packages/drift/__init__.py` 注释明确“阈值来自 `configs/drift.yaml`”（该文件尚未落地，仅作为规范引用）

## 3. 架构与设计约定
- 分层策略
  - `base.yaml` 承载不可变默认值；每个环境（dev/prod）以独立 YAML 覆盖层 `extends` 基配置。
  - 敏感/易变项（数据库 URL、对象存储凭据、MLflow URI）一律通过 `${ENV_VAR}` 占位符在覆盖层中注入，禁止硬编码。
- 双轨加载模式
  - 结构化配置：YAML 描述领域开关（markets、risk_engine.layers、model_registry.states、mcp.ban 等）。
  - 进程内直读：API/Worker/MCP Server 直接用 `os.getenv` 拉取连接串与开关，避免引入集中式 Config 单例。
- 可观测性与可运维性
  - 日志级别由 `LOG_LEVEL` 控制，统一经 `packages/common/log.py` 的 structlog JSON 输出，并自动注入 trace_id/request_id。
  - Prometheus `/metrics` 端点由 FastAPI lifespan 暴露，配合 docker-compose 中的 prometheus/grafana 服务。
- 数据与模型生命周期
  - Alembic 管理 schema 演进；drift 检测阈值预留 `configs/drift.yaml` 作为未来扩展点。

## 4. 开发者应遵循的规则
- 新增配置项优先放入 `configs/base.yaml`，并在对应环境覆盖层（如 `dev.yaml`）用 `${ENV_VAR}` 注入实际值。
- 敏感信息只出现在 `.env` 或 CI secret 中，绝不提交到版本库；参考 `deploy/.env.example` 维护完整清单。
- 进程间共享的外部依赖（Postgres、Redis、MinIO、MLflow）地址统一通过环境变量传递，保持 compose 与应用解耦。
- 对业务开关（风险层级、MCP 禁令、LLM 权限）采用 YAML 布尔/列表形式，便于热更新与审计；不要在代码里写死分支判断。
- 新增可选能力时，沿用 `pyproject.toml` 的 `[project.optional-dependencies]` 分组，按需安装最小依赖集。
- 若需要新的 YAML 配置段，先在模块 docstring 中声明来源路径（参照 `packages/drift/__init__.py` 风格），再实现加载逻辑。