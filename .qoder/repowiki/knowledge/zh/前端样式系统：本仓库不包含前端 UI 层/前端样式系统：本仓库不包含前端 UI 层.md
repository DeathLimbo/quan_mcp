---
kind: frontend_style
name: 前端样式系统：本仓库不包含前端 UI 层
category: frontend_style
scope:
    - '**'
---

经全仓检索，该仓库为纯后端/量化引擎工程（FastAPI + Python packages），未发现任何 CSS、SCSS、HTML 模板或前端框架（Tailwind、Bootstrap、AntD 等）相关代码与配置。所有对外交互均为 JSON API（`apps/api/main.py` 暴露 `/v1/*` REST 路由与 Prometheus `/metrics`），报告渲染通过 `packages/reporting/render.py` 输出 Markdown 文本，而非 HTML/CSS 页面。因此 `frontend_style` 类别不适用于此仓库。