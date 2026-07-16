---
kind: build_system
name: 构建与依赖管理：Hatch + PyProject 单仓多包体系
category: build_system
scope:
    - '**'
source_files:
    - pyproject.toml
    - .pre-commit-config.yaml
    - deploy/docker-compose.yml
    - alembic.ini
    - sql/migrations/env.py
---

本仓库采用基于 pyproject.toml 的纯 Python 构建方案，使用 Hatchling 作为后端、Ruff/Black/Mypy 作为代码质量工具链，并通过 Docker Compose 编排本地开发环境。不存在 Makefile、Dockerfile、CI YAML 或 requirements.txt 等外部构建脚本。

1. 构建系统与打包
- 构建后端：hatchling.build（见 [build-system]），通过 hatch build / pip install . 触发。
- 包结构：packages 与 apps 两个顶层目录同时被纳入 wheel 包（[tool.hatch.build.targets.wheel].packages = ["packages", "apps"]），形成“单仓多包”布局，每个子目录即一个领域包（如 packages/features、apps/api）。
- 版本：项目级单一版本号 0.1.0，未启用 hatch 的版本插件，各子包不独立发版。

2. 依赖管理与可选特性
- 核心依赖集中在根 pyproject.toml 中，按功能拆分为 optional-dependencies：
  - ml：scikit-learn、lightgbm、mlflow
  - backtest：vectorbt、numba
  - data-sources：akshare、yfinance、requests
  - dev：pytest、ruff、black、mypy、pre-commit 等
- 安装示例：pip install ".[ml,dev]"；生产镜像可通过只装 core + data-sources 缩小体积。
- Python 要求：>=3.11，所有依赖均用宽松范围 pin（如 fastapi>=0.111,<0.140），便于在 py3.13 上获取 wheel。

3. 代码质量与静态检查
- Ruff：lint 规则集 E/F/I/N/UP/B/A/C4/SIM/RUF，忽略 E501（行宽由 Black 控制）；.pre-commit-config.yaml 中 ruff 带 --fix 自动修复。
- Black：统一格式化，line-length=100，target-version=py311。
- Mypy：strict 模式运行于 packages、apps，对 akshare/yfinance/vectorbt/lightgbm/mlflow/minio 等第三方库关闭缺失 stub 报错。
- Pre-commit：提交前依次执行 ruff、ruff-format、trailing-whitespace、end-of-file-fixer、check-yaml/toml、large-files(≤1MB)、merge-conflict、mixed-line-ending(LF)，以及本地 mypy。

4. 测试与覆盖率
- pytest 配置在 [tool.pytest.ini_options]，testpaths=tests，标记包括 integration（需 docker-compose）、replay（慢回放）、golden（黄金数据集回归）。
- coverage 源为 packages、apps，开启 branch 覆盖并跳过已覆盖文件。

5. 数据库迁移与运行时配置
- Alembic：alembic.ini + sql/migrations/versions/*.py 手写迁移，按时间戳命名（如 20260715_0001_instruments.py）。
- 运行时配置：configs/base.yaml、configs/dev.yaml 配合 pydantic-settings 注入。

6. 本地环境与容器编排
- deploy/docker-compose.yml 一键拉起 Postgres 16、Redis 7、MinIO、MLflow、Prometheus、Grafana，提供健康检查与端口映射，供 API/Worker/MCP Server 本地联调。
- 无 Dockerfile：应用以源码方式挂载到 compose stack 或通过 pip install . 安装，未做镜像分层构建。

7. 开发者约定
- 新增依赖必须写入根 pyproject.toml 对应 extra，禁止在项目内维护 requirements.txt。
- 新增包应放在 packages/<domain> 或 apps/<service>，并在 [tool.hatch.build.targets.wheel].packages 中注册。
- 提交前必须通过 pre-commit；CI 若接入建议复用同一份 ruff/black/mypy/pytest 配置。
- 数据库变更走 Alembic 迁移，禁止直接写 DDL。