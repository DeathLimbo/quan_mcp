---
kind: build_system
name: 构建与制品管理（Hatch + docker-compose 单仓）
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

本仓库采用 Python 单仓聚合模式，以 pyproject.toml 为唯一构建入口，使用 Hatchling 作为后端统一打包、依赖声明与工具链配置；本地开发通过 deploy/docker-compose.yml 一键拉起 Postgres、Redis、MinIO、MLflow、Prometheus/Grafana 等基础设施。

1. 构建系统
- 构建后端：hatchling.build，要求 Python >=3.11。
- 包目标：packages 与 apps 两个目录同时打入 wheel，形成跨市场量化平台单体可分发包。
- 依赖分组：核心依赖 + ml/backtest/data-sources/dev 四个可选 extras，便于按需安装 worker、MCP 等子应用的最小运行时。

2. 代码质量与预提交
- Ruff（lint+format）、Black、mypy（strict 模式，对 akshare/yfinance/vectorbt/lightgbm/mlflow/minio 等第三方放宽）在 [tool.*] 中集中声明。
- .pre-commit-config.yaml 集成 ruff-pre-commit、pre-commit-hooks 以及本地 mypy hook，提交前自动修复格式、检查 YAML/TOML、大文件与合并冲突。

3. 测试与覆盖率
- pytest 8.x，tests 为根测试路径，标记 integration/replay/golden 三类用例；coverage 统计 packages+apps 分支覆盖并报告缺失行。

4. 数据库迁移
- Alembic 迁移脚本位于 sql/migrations/versions/，由 alembic.ini 驱动，配合 env.py 与 script.py.mako 模板生成版本化 DDL。

5. 容器编排与本地环境
- deploy/docker-compose.yml 定义 postgres、redis、minio、mlflow、prometheus、grafana 六个服务，提供健康检查与端口映射，配合 configs/base.yaml、configs/dev.yaml 实现分层配置。
- 当前仓库未包含独立 Dockerfile，镜像直接复用官方 image；如需自构建镜像，可在根或各 app 目录下新增 Dockerfile 并通过 compose build 引入。

6. 开发者约定
- 新增依赖优先放入对应 extra，避免污染最小运行集。
- 所有 lint/format/type-check 规则集中在 pyproject 与 pre-commit，禁止在 IDE 中绕过。
- 测试按 unit/integration/golden 分层，新特性需补充对应标记用例。
- 数据库变更通过 Alembic 增量迁移，不得手写 DDL 脚本。