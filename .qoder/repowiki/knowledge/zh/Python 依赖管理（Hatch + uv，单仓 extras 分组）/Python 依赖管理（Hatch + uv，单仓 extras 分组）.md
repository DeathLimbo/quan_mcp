---
kind: dependency_management
name: Python 依赖管理（Hatch + uv，单仓 extras 分组）
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - .gitignore
    - README.md
---

## 1. 使用的系统/工具
- 包清单与构建：`pyproject.toml` + `hatchling` 构建后端，使用 PEP 621 声明式依赖。
- 虚拟环境与安装：README 推荐 `uv`（`uv venv`、`uv pip install -e ".[dev,ml,backtest,data-sources]"`），未检出 `uv.lock`，当前无锁定文件。
- 无 vendoring、私有 PyPI 源或 `pip.conf` 配置；依赖全部来自公共 PyPI。

## 2. 关键文件
- `pyproject.toml`：唯一依赖声明入口，包含运行时依赖、extras 分组、构建系统与工具链配置。
- `.gitignore`：忽略 `.venv/`、`venv/`、`build/`、`dist/`、`.pytest_cache/`、`artifacts`。
- `README.md`：开发环境安装指引（uv + hatch）。