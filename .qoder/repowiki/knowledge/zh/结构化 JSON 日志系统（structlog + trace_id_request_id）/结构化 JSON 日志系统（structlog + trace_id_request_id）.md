---
kind: logging_system
name: 结构化 JSON 日志系统（structlog + trace_id/request_id）
category: logging_system
scope:
    - '**'
source_files:
    - packages/common/log.py
    - packages/common/__init__.py
    - apps/api/main.py
    - apps/worker/main.py
---

本仓库采用基于 structlog 的结构化 JSON 日志方案，所有应用进程通过统一入口初始化，并在请求/任务上下文中自动注入 trace_id 与 request_id，实现跨服务链路追踪。

## 1. 使用的框架与工具
- structlog：作为核心日志库，提供上下文变量合并、处理器链与 JSON 渲染能力。
- logging：仅作为底层 handler，由 structlog 的 PrintLoggerFactory 输出到 stdout。
- contextvars.ContextVar：用于在异步/并发上下文中传递 trace_id / request_id。

## 2. 核心文件与包
- packages/common/log.py：日志配置、上下文注入、logger 工厂函数集中定义。
- packages/common/__init__.py：将 get_logger、bind_trace、configure_logging 暴露为公共 API。
- apps/api/main.py：FastAPI 启动时调用 configure_logging()，HTTP 中间件通过 bind_trace 注入 trace_id/request_id。
- apps/worker/main.py：RQ worker 启动时同样调用 configure_logging()，以相同格式记录任务日志。

## 3. 架构与约定
- 统一初始化：每个进程入口（API、Worker）在启动阶段调用 configure_logging(level=None)，从环境变量 LOG_LEVEL 读取级别，默认 INFO；stdout 单行 JSON 输出。
- 处理器链顺序：merge_contextvars → add_log_level → TimeStamper(fmt="iso", utc=True) → _inject_trace → StackInfoRenderer → format_exc_info → JSONRenderer。
- 过滤级别：通过 make_filtering_bound_logger(getattr(logging, lvl)) 在 structlog 层做级别过滤，避免下游 handler 重复过滤。
- trace_id/request_id 注入：API 中间件从请求头 x-trace-id / x-request-id 读取，缺失则生成 UUID hex，并通过 bind_trace 写入 ContextVar；响应头回写相同的两个 ID；Worker 侧未显式设置，但可通过任务参数或上层调度器传入。
- 命名约定：使用 get_logger("api") / get_logger("worker") 等短名称区分模块，默认根名为 "quant"。

## 4. 开发者应遵循的规则
- 不要直接 import logging.getLogger：统一通过 from packages.common import get_logger 获取 structlog BoundLogger。
- 不要在业务代码中手动 configure_logging：仅在进程入口（main/lifespan）调用一次。
- 在 HTTP 处理路径中必须绑定 trace_id/request_id：API 中间件已自动完成，业务 handler 无需重复。
- 日志事件使用 keyword arguments：如 logger.info("api.quant_error", code=e.code.value, path=request.url.path)，保持结构化 JSON 可读性。
- 敏感信息不入日志：trace_id/request_id 是标识符，不应包含用户隐私或密钥。
- 日志级别策略：INFO 用于关键流程节点，WARNING 用于可恢复异常（QuantError），ERROR/exception 用于不可恢复错误。