---
kind: dependency_management
name: Python 依赖管理（Hatch + pyproject.toml）
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
---

本仓库采用 Python 生态标准的单仓多包结构，统一通过根目录的 pyproject.toml 声明全部第三方依赖与可选依赖组，构建后端使用 Hatchling。

1. 使用的系统与工具
- 包管理与构建：Hatch（hatchling），由 [build-system] 指定；无 requirements.txt、Pipfile、poetry.lock、uv.lock 等锁文件或替代清单。
- 运行时约束：requires-python = ">=3.11"，所有依赖以宽松范围（如 >=0.111,<0.140）声明，注释中明确“pragmatic pin ranges”策略。
- 可选依赖分组：[project.optional-dependencies] 将 ML（scikit-learn/lightgbm/mlflow）、回测（vectorbt/numba）、数据源（akshare/yfinance/requests）、开发（pytest/ruff/black/mypy/pre-commit）拆为独立 extras，供不同应用按需安装。
- 代码质量与类型检查：Ruff、Black、Mypy 配置集中在同一文件，Mypy 对 akshare/yfinance/vectorbt/lightgbm/mlflow/minio 等第三方库显式 ignore_missing_imports。

2. 关键文件
- pyproject.toml：唯一依赖声明入口，包含项目元信息、核心依赖、extras、构建目标（packages/apps 两个命名空间）、以及 ruff/black/mypy/pytest/coverage 工具链配置。
- deploy/docker-compose.yml：运行期编排（Postgres、Redis、MinIO、Prometheus），不直接声明 Python 依赖，但配合镜像/容器环境消费上述依赖。
- alembic.ini / sql/migrations/：数据库迁移脚本，依赖 SQLAlchemy + Alembic，版本随 pyproject.toml 中的范围约束。

3. 架构与约定
- 单仓多包：packages/* 为领域包（如 data_sources、features、inference、risk 等），apps/* 为可执行服务（FastAPI、Worker、MCP Server），均由根 pyproject.toml 统一分发，避免每个子包重复声明依赖。
- 依赖分层：核心运行时（FastAPI、Pydantic v2、SQLAlchemy 2、Polars/Pandas、orjson、structlog、prometheus-client、minio、mcp SDK）放在顶层 dependencies；实验性/重型能力（ML、回测、外部数据源）放入 optional extras，降低默认安装体积。
- 版本策略：注释强调“第一版依赖集对齐实现计划 V4”，采用“宽松上界 + 下界锁定”的方式平衡升级灵活性与兼容性，未引入 lockfile 做精确复现。
- 私有源/代理：仓库内未发现任何 --index-url、PIP_*_INDEX、pip.conf 或私有 registry 相关配置，默认走 PyPI。

4. 开发者应遵循的规则
- 新增依赖一律在根 pyproject.toml 的对应 section 声明，不要新建 requirements.txt 或子包 setup.py。
- 属于特定能力的依赖放入相应 optional-dependencies（ml/backtest/data-sources/dev），并在调用方通过 pip install cross-market-quant[xxx] 安装。
- 保持范围约束风格一致：下界用 >=X.Y，上界用 <Y+1 或更紧的上界，并在注释中说明放宽原因（如 “relaxed for mcp sdk (starlette 1.x)”）。
- 若引入缺少类型存根的第三方库，需在 [tool.mypy.overrides] 中追加 ignore_missing_imports。
- 如需固定精确版本用于发布或 CI 复现，应在构建/CI 层生成并校验 lockfile（当前仓库未内置该流程）。