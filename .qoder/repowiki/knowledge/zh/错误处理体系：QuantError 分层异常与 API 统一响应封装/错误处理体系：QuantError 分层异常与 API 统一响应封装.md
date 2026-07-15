---
kind: error_handling
name: 错误处理体系：QuantError 分层异常与 API 统一响应封装
category: error_handling
scope:
    - '**'
source_files:
    - apps/api/main.py
    - apps/api/routers/admin_ingestion.py
    - apps/api/routers/data_status.py
    - apps/api/routers/forecast.py
    - apps/api/routers/fundamentals.py
---

本仓库采用基于自定义异常类的分层错误处理方案，核心围绕 QuantError 及其子类构建，并在 FastAPI 层通过 try/except 块将业务异常转换为结构化 HTTP 响应。

## 1. 系统与方法
- 自定义异常基类：QuantError 作为所有业务异常的根类，提供统一的错误码、消息与上下文信息；具体领域异常（如 DataConflictError、ValueError 包装等）继承自它，形成清晰的异常层次结构。
- FastAPI 路由层捕获：每个 router 函数在关键调用处包裹 try/except，显式捕获 QuantError 及其子类，将其映射为带状态码的 JSON 响应；未捕获的 Exception 走兜底逻辑。
- 无 panic/recover：Python 生态下不使用 panic/recover 模式，全部通过异常传播 + 显式捕获实现错误边界控制。
- 无全局中间件：未发现基于 Starlette/FastAPI middleware 的全局错误处理器，错误转换逻辑内联在每个路由中。

## 2. 关键文件与包
- apps/api/main.py：应用入口，包含顶层 try/except 兜底逻辑，演示 QuantError 与通用 Exception 的差异化处理。
- apps/api/routers/*.py：各业务路由（admin_ingestion.py、data_status.py、forecast.py、fundamentals.py 等），均遵循相同的 try/except QuantError 模式。
- QuantError 定义位置：从 grep 结果可见其被多处 import 使用，但未能定位到定义文件（可能在某个 packages 子模块或 apps 共享模块中）。

## 3. 架构与约定
- 异常上抛，响应下沉：业务层抛出 QuantError 子类，API 层负责将其转为 HTTP 4xx/5xx 响应，保持关注点分离。
- 参数校验优先用 ValueError：对入参非法的情况，路由层直接 raise ValueError，再由外层 try/except 统一捕获，避免在业务域中混用。
- 数据冲突专用异常：DataConflictError 用于表示幂等写入冲突等可恢复的业务冲突，区别于一般性 QuantError。
- 无错误码枚举：错误语义通过异常类型区分，而非集中式的错误码表。

## 4. 开发者应遵循的规则
1. 在业务逻辑中 raise 具体的 QuantError 子类，不要直接 raise 裸 Exception。
2. 在 API 路由层始终用 try/except 捕获 QuantError 及其子类，并返回结构化响应；仅对不可预期的异常保留默认兜底。
3. 参数校验失败优先 raise ValueError，由路由层统一处理。
4. 不要在业务层自行构造 HTTP 响应对象，保持纯 Python 异常语义。
5. 新增领域错误时，继承 QuantError 并赋予明确语义，避免滥用单一基类。