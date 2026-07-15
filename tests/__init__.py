"""Tests root. Layout matches spec §测试.

- unit/         pure-logic, no IO
- contract/     JSON-Schema of MCP tools and API envelope
- integration/  requires postgres/redis/minio (docker compose up)
- replay/       golden-dataset regression
- e2e/          full skill+mcp+api+worker path
"""
