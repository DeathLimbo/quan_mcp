"""quant-admin-mcp: admin/write MCP server.

Sensitive operations (model publish, kill-switch, rollback). Every write is:
- gated by dual-control approval (`approval_id` mandatory for PRODUCTION),
- audited (append-only, hash-chained via packages.audit),
- reversible when possible (rollback recovers previous PRODUCTION).

This module intentionally does NOT expose ordering / execution. Order routing
lives elsewhere behind the risk engine.
"""
