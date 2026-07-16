---
kind: logging_system
name: 结构化 JSON 日志系统（structlog + ContextVar 链路追踪）
category: logging_system
scope:
    - '**'
source_files:
    - packages/common/log.py
    - packages/common/__init__.py
    - apps/api/main.py
    - apps/worker/main.py
    - pyproject.toml
---

## 1. 使用的框架与工具
- **核心库**：`structlog>=24.1,<25.0`，配合 Python 标准库 `logging` 作为后端。
- **输出格式**：JSON（`structlog.processors.JSONRenderer`），时间戳 ISO 8601 UTC，包含堆栈信息与异常信息。
- **链路追踪**：基于 `contextvars.ContextVar` 注入 `trace_id` / `request_id`，跨进程/线程自动携带。
- **日志级别**：通过环境变量 `LOG_LEVEL` 控制，默认 `INFO`，使用 `make_filtering_bound_logger` 在 structlog 层做过滤。
- **输出目标**：stdout（`PrintLoggerFactory`），适合容器化部署，由外部收集器（如 Docker、K8s、Prometheus 生态）统一采集。

## 2. 关键文件与包
- `packages/common/log.py` — 日志配置、上下文变量注入、logger 工厂函数。
- `packages/common/__init__.py` — 将 `get_logger`、`bind_trace`、`configure_logging` 暴露为公共 API。
- `apps/api/main.py` — FastAPI 启动时调用 `configure_logging()`；HTTP 中间件从请求头提取并绑定 `trace_id`/`request_id`；统一记录量化错误与内部异常。
- `apps/worker/main.py` — RQ Worker 启动时同样调用 `configure_logging()`，以 `worker` 命名空间获取 logger。
- `pyproject.toml` — 声明 `structlog>=24.1,<25.0` 依赖。

## 3. 架构与设计约定
- **集中式初始化**：所有应用入口（API、Worker）在生命周期开始时调用 `configure_logging(level=None)`，仅初始化一次，后续通过 `get_logger(name)` 复用。
- **结构化字段**：每条日志自动附带 `level`、`timestamp`、`stack_info`、`exception`（如有）、以及业务上下文字段（如 `path`、`code`、`redis_url`、`queues` 等）。
- **链路追踪贯穿**：
  - HTTP 请求进入时，中间件读取 `x-trace-id` / `x-request-id` 请求头（不存在则生成 UUID hex），写入 `ContextVar`。
  - `_inject_trace` processor 在每次渲染前把这两个字段合并到 event_dict，保证所有下游 logger 输出都带 trace。
  - 响应头回写 `x-trace-id` / `x-request-id`，便于客户端关联。
- **命名空间策略**：按子系统划分 logger name（`api`、`worker`、`quant` 默认），便于按模块筛选日志。
- **异常处理**：API 层对 `QuantError` 用 `logger.warning` 记录业务错误，对未捕获异常用 `logger.exception` 记录完整堆栈，避免吞掉异常信息。

## 4. 开发者应遵循的规则
1. **不要直接 import logging**：统一通过 `from packages.common import get_logger, bind_trace, configure_logging` 使用。
2. **应用启动时调用 `configure_logging()`**：确保 structlog 处理器链已注册，否则日志不会以 JSON 输出。
3. **在 HTTP 请求入口处绑定 trace**：API 中间件已自动完成，业务代码无需重复；如需手动设置，调用 `bind_trace(trace_id=..., request_id=...)`。
4. **使用结构化键值而非字符串拼接**：例如 `logger.info("task.done", duration=1.2, items=100)`，让 JSON 可被下游解析。
5. **合理选择日志级别**：业务异常用 `warning`，程序错误用 `exception`，调试信息用 `debug`，生产默认 `INFO` 以上。
6. **不要在子进程中重新初始化日志**：structlog 使用 `cache_logger_on_first_use=True`，子进程继承父进程配置即可。