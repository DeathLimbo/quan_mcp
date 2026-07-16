---
kind: error_handling
name: 统一错误体系与 API 异常包装
category: error_handling
scope:
    - '**'
source_files:
    - packages/common/errors.py
    - packages/common/response.py
    - packages/common/__init__.py
    - apps/api/main.py
    - packages/models/registry.py
---

本仓库采用「枚举错误码 + 基类异常 + FastAPI 中间件」三层架构，为所有 API 与 MCP Tool 提供一致的错误定义、传播与呈现方式。

## 1. 系统/框架概览
- 错误类型：packages/common/errors.py 中通过 ErrorCode 枚举集中声明所有业务错误码，并用 _mk 工厂动态生成对应的 *Error 子类（如 DataConflictError、UnknownInstrumentError），全部继承自 QuantError。
- 响应信封：packages/common/response.py 定义 ApiResponse、ok()、err()，保证成功/失败 JSON 结构一致。
- 统一入口：apps/api/main.py 的 HTTP 中间件 trace_and_envelope 捕获所有异常，把 QuantError 映射到 400，其他异常映射到 500，并注入 x-trace-id / x-request-id。

## 2. 关键文件与包
- packages/common/errors.py — 错误码枚举与 QuantError 基类，以及 16 个具体错误类。
- packages/common/response.py — ApiResponse Pydantic 模型及 ok/err 构造器。
- packages/common/__init__.py — 将错误、响应、日志、时间工具统一从 packages.common 暴露。
- apps/api/main.py — FastAPI 应用启动、中间件注册、路由挂载。
- packages/models/registry.py — 领域内自定义异常 ModelTransitionError(QuantError) 的典型用法。
- 各 router (apps/api/routers/*.py) — 在业务层 raise 具体 *Error，由中间件统一包装。

## 3. 架构与约定
- fail-closed 策略：注释明确列出 Data / Universe / Session / Model / Risk / AuthZ / Ops 七类错误域，新增错误需先在 ErrorCode 登记再派生。
- 机器可读错误码：每个 *Error 绑定一个稳定字符串 code，客户端可据此做分支处理；人类可读信息放在 message，调试上下文放在 details。
- 异常传播路径：
  - 业务层 -> raise <SpecificError>(msg, details=...)
  - Router 层 -> 可选择性 catch 后重新 raise 或记录日志
  - 中间件 -> 捕获 QuantError 返回 400 + err(e)，捕获 Exception 返回 500 + err(e)
- Trace 贯穿：中间件读取/生成 x-trace-id、x-request-id，写入请求与响应头，并通过 bind_trace 注入到结构化日志。

## 4. 开发者应遵循的规则
1. 不要直接 raise 裸 Exception：业务错误一律使用 packages.common 导出的具体 *Error；仅在不可恢复的内部故障时让中间件兜底。
2. 错误码必须来自 ErrorCode：新增场景先在 errors.py 添加枚举值，再用 _mk 生成的类名，避免手写字符串。
3. 携带 details：对需要下游定位的问题（如缺失字段、冲突键）在 details 中附带结构化数据。
4. Router 层只负责边界校验：参数解析失败用 ValueError 即可，会被中间件转为 500；业务语义错误才抛 QuantError 子类。
5. MCP Tool 同样适用：apps/quant-admin-mcp/tools.py 中 ModelTransitionError 的使用表明该体系同时覆盖 MCP 工具调用。
6. 禁止在业务层自行构造 JSONResponse：统一走 ok() / err()，确保 trace_id、request_id、warnings 等字段一致。