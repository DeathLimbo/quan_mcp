# 部署文档 (Deployment Guide)

> 目标读者：SRE / 平台工程师 / 负责首次上线与日常运维的人。
> 覆盖范围：**dev（单机）/ staging（预演）/ prod（生产）**。
> 权威源：本目录 + [`readme/A股美股基金量化Agent_Skill+MCP模块实施规格_V4.md`](../readme/A股美股基金量化Agent_Skill+MCP模块实施规格_V4.md) §98（基础设施）/ §106（可观测性）。

---

## 1. 组件拓扑

```
                +--------------------+
    users --->  |  quant-api  :8000  |----+
                +--------------------+    |
                                          |
                +--------------------+    |    +--------------------+
                | quant-worker :8001 |----+--> |  postgres  :5432   |
                +--------------------+    |    +--------------------+
                                          |
                +--------------------+    |    +--------------------+
                |  scheduler / cron  |----+--> |    redis   :6379   |
                +--------------------+    |    +--------------------+
                                          |
                +--------------------+    |    +--------------------+
                | quant-read-mcp     |----+--> |    minio   :9000   |
                | quant-admin-mcp    |         +--------------------+
                +--------------------+
                                               +--------------------+
                                               |   mlflow   :5000   |
                                               +--------------------+
                        prometheus :9090  <--- /metrics (api & worker)
                        grafana    :3000  <--- prometheus
```

**核心不变量**（生产必须保证）：
- Postgres 是唯一的持久事实源；MinIO 只放 artifact（模型权重、报告 PDF、Parquet 快照）。
- 任何服务重启不得导致预测请求返回错误结果 — 失败必须 `NO_FORECAST / NO_TRADE`，永不静默降级。
- 审计流永远只增不改（`packages.audit`）；数据库层禁 DELETE / UPDATE on `audit_events`。

---

## 2. 环境矩阵

| 关键项 | dev | staging | prod |
|---|---|---|---|
| 部署方式 | `docker compose` 单机 | k8s 单副本 or compose | k8s 多副本，跨可用区 |
| Postgres | compose 容器，pgdata volume | 托管实例，快照每日 | 托管 HA（主+2 副本），PITR |
| Redis | compose 容器 | 单节点持久化 | Sentinel 或 Cluster，AOF |
| MinIO / S3 | compose 内 MinIO | MinIO + 快照 | S3 + Object Lock |
| MLflow | compose | 单实例 + PG 后端 | 双实例 + LB |
| Prometheus | compose | 单实例 15d 保留 | 双实例，远程写至长期存储 |
| 密钥来源 | `.env` 文件 | Vault dev token | Vault / KMS |
| 数据许可流量 | 无 | 只读订阅 | 有 TOS 合约的真实源 |
| 出网限制 | 无 | 只允许 whitelist 域名 | 只允许 whitelist 域名 |

---

## 3. Secrets 契约

**规则**：仓库里**不允许**出现任何真实密钥；`deploy/.env.example` 是唯一权威变量清单。

必需环境变量（全部服务共用）：

| 变量 | 用途 | dev 默认 | prod 要求 |
|---|---|---|---|
| `APP_ENV` | 环境标签，进入所有日志与指标 | `dev` | `prod` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Postgres 凭据 | `quant/quant/quant` | 长随机；密文托管 |
| `DATABASE_URL` | 应用连接串（driver = psycopg v3，与 pyproject.toml 一致） | `postgresql+psycopg://quant:quant@postgres:5432/quant` | Vault 注入 |
| `REDIS_URL` | 队列/缓存 | `redis://redis:6379/0` | TLS，凭据从 Vault |
| `S3_ENDPOINT_URL` | 对象存储 | `http://minio:9000` | AWS/Aliyun endpoint |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 对象存储凭据 | `minio/minio12345` | KMS / IAM Role |
| `S3_BUCKET_ARTIFACT` | 模型 artifact bucket | `artifact` | 独立、启用版本控制 |
| `MLFLOW_TRACKING_URI` | MLflow 服务 | `http://mlflow:5000` | 内网 HTTPS |
| `AKSHARE_TIMEOUT_SECONDS` | AKShare 拉取超时 | `10` | `30` |
| `YFINANCE_TIMEOUT_SECONDS` | yfinance 拉取超时 | `10` | `30` |
| `LICENSE_TAG_DEFAULT` | 无法识别源时的许可标签 | `INTERNAL_RESEARCH` | `INTERNAL_RESEARCH`（禁生产商用） |
| `RUN_LIVE_ADAPTERS` | 单测是否跑真拉取 | 未设置 | 未设置（CI 用 `1`） |

`.env.example` 会与本文件一起提交，参见同目录。

---

## 4. 首次上线（dev / 单机）

```powershell
# 1) 依赖
python -m pip install uv
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev,ml,backtest,data-sources]"

# 2) 复制环境模板
Copy-Item deploy\.env.example deploy\.env

# 3) 启动基础设施栈
docker compose --env-file deploy\.env -f deploy\docker-compose.yml up -d

# 4) 等 Postgres healthcheck 通过（约 15s）
docker inspect --format="{{.State.Health.Status}}" quant-postgres

# 5) 数据库迁移（在仓库根目录执行，alembic.ini 与 sql/migrations 相对路径已配好）
$env:DATABASE_URL="postgresql+psycopg://quant:quant@localhost:5432/quant"
alembic upgrade head

# 6) 启 API + Worker
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
# 另一个终端：
python -m apps.worker.main

# 7) 健康检查
curl http://localhost:8000/v1/health
curl http://localhost:8000/metrics | Select-String api_requests_total
```

**验收**：
- `/v1/health` 返回 `{"status":"ok",...}`
- Grafana `http://localhost:3000` 登录 `admin / admin` 后能看到 Prometheus 数据源
- MLflow `http://localhost:5001` 可访问
- MinIO console `http://localhost:9001` 登录 `minio / minio12345` 后有 `artifact` bucket（首次跑训练任务后创建）

---

## 5. 数据库迁移

**统一入口**：Alembic，配置在 [`sql/migrations/env.py`](../sql/migrations/env.py)。

| 场景 | 命令 |
|---|---|
| 升到最新 | `alembic upgrade head` |
| 升到指定 | `alembic upgrade <rev>` |
| 回滚一步 | `alembic downgrade -1` |
| 查看当前 | `alembic current` |
| 查看历史 | `alembic history` |
| 生成新 revision（手工审阅） | `alembic revision -m "add xxx"` |

**上线顺序（生产）**：
1. 冻结写入（把 API 切到只读维护页 or 拒绝管理写）；
2. `pg_dump` 全量备份 + WAL 归档确认；
3. `alembic upgrade head`；
4. 灰度：把 API 切到"影子读"（同时写新旧列一段时间）；
5. 观察 1-24h，无异常再切主流；
6. 保留旧列至少 7 天再删。

**当前 8 个 migration** 都放在 [`sql/migrations/versions/`](../sql/migrations/versions/)：
`0001_instruments → 0002_audit_events → 0003_market_bar → 0004_corporate_action → 0005_fundamental_fact → 0006_fund_fx_portfolio → 0007_market_bar_provenance → 0008_ca_nav_provenance`。

---

## 6. 回滚（Rollback）

**代码回滚**（推荐先做这步）：
1. 保留最近 3 个 tag 的镜像；
2. `docker compose -f deploy/docker-compose.yml pull <service>:<prev_tag>` 后 `up -d`。

**数据库回滚**：
- 首选 **前向补丁**（写一个新的 revision 撤销/修复），而不是 `downgrade`。原因：`downgrade` 会丢弃列上的数据，风控/审计不可接受。
- 只有在 migration 5 分钟内且**无生产写入**才允许 `alembic downgrade -1`。

**Registry 回滚**（模型上线出错）：走 `apps.quant-admin-mcp` 的 `model_start_shadow` / `promote_model` 反向流程；不允许绕过 §81.1 gate 直接改数据库状态。

---

## 7. 备份与恢复

| 数据类型 | 保留 | 频率 | 恢复演练 |
|---|---|---|---|
| Postgres 全量 | ≥ 30 天 | 每日 02:00 UTC | 每季度真跑一次 restore |
| Postgres WAL | ≥ 7 天 | 连续归档 | PITR 演练每季度 |
| MinIO / S3 artifact | 永久（版本化） | 事件触发 | 半年 |
| Prometheus | 15 天（本地） / 400 天（远程） | 15s scrape | 无需 |
| audit_events 表 | 永久 | 随 PG 全量 | 每季度对账 |

**恢复目标**（生产 SLO）：
- **RTO**（recovery time）：2 小时
- **RPO**（recovery point）：15 分钟（WAL 连续归档）

---

## 8. 监控与告警

### 指标（`/metrics`）
- `api_requests_total{path,status}` — 请求计数
- `api_request_seconds` — 延迟直方图（p50/p95/p99）
- （后续按 spec §106 补：`ingestion_lag_seconds`, `feature_pit_violations_total`, `model_gate_rejections_total`, `data_quality_status`）

### Grafana 面板（生产必须存在）
1. **API SLO**：RPS / 错误率 / p95 延迟
2. **数据 ingestion**：source × lag / 数据质量分布
3. **模型运行时**：per-model prediction rate / gate rejections / shadow-vs-prod divergence
4. **风控**：`NO_FORECAST` / `NO_TRADE` 计数
5. **审计**：write rate / gap detection

### 告警（Prometheus rules）
| 告警 | 条件 | 严重度 |
|---|---|---|
| `APIErrorRateHigh` | `rate(api_requests_total{status=~"5.."}[5m]) > 0.02` | P1 |
| `IngestionLagHigh` | `ingestion_lag_seconds > 600` for 10m | P2 |
| `FeaturePITViolation` | `increase(feature_pit_violations_total[15m]) > 0` | **P0** |
| `ModelGateSpike` | `rate(model_gate_rejections_total[1h]) > 5` | P2 |
| `AuditWriteStalled` | `rate(audit_events_written[5m]) == 0 for 15m` | P1 |

> **P0** = 立即失效相关模型 & 冻结相关数据源，等人工介入。

---

## 9. 数据源治理（Provenance & License）

上线前必须核对每个 adapter 的 `source_version` / `license_tag`：

| Adapter | source_version | license_tag | prod 允许？ |
|---|---|---|---|
| `AkshareAdapter` | `akshare.v1` | `PROVIDER_TOS` | 需商用 TOS 合同 |
| `YfinanceAdapter` | `yfinance.v1` | `PROVIDER_TOS` | 需 Yahoo 商用合同 |
| CSV/内部快照 | 自定 | `INTERNAL_RESEARCH` | **禁止**用于对外产品 |

`quality_status` 只有 `NORMAL` 才允许进入生产推理路径；`STALE / SUSPECT / QUARANTINED` 一律触发 `NO_FORECAST`。

---

## 10. 常见故障排查

| 症状 | 首步排查 | 常见根因 |
|---|---|---|
| API 启动即 500 | `docker logs quant-api` | `DATABASE_URL` 拼错 / Postgres 未 healthy |
| `alembic upgrade` 报 `Target database is not up to date` | `alembic current` | 分叉 revision，先 `merge heads` |
| MinIO 403 | 检查 bucket 是否存在 | 首启需手动 `mc mb minio/artifact` |
| Prometheus 抓不到 target | `http://prometheus:9090/targets` | `host.docker.internal` 在 Linux 需加 `--add-host` |
| MLflow 500 保存 artifact | 看 MLflow 日志 | `AWS_ACCESS_KEY_ID` / bucket 权限 |
| `/v1/forecast` 全 `NO_FORECAST` | 看 audit 事件的 `reason` 字段 | 通常是 data_quality 或 gate 拒绝，非 bug |
| CI live adapter 测试 skip | 期望行为 | 生产 CI 才应 `RUN_LIVE_ADAPTERS=1` |

---

## 11. 灾备与演练

**每季度必做**（生产）：
1. **数据库 restore drill**：从最近备份 restore 到隔离环境，跑 `alembic current` + 关键表 row-count 对账。
2. **模型回滚演练**：故意把一个 `PRODUCTION` 模型降级至 `SHADOW`，确认预测流量正确切换。
3. **数据源降级演练**：切断 AKShare / yfinance 出网，确认系统进入 `NO_FORECAST` 而不是使用陈旧数据。
4. **审计对账**：`audit_events` 的 `sequence_no` 无跳号；与 Postgres binlog 对齐。

---

## 12. 上线检查清单（Production Go-Live）

- [ ] `.env` 从 Vault 生成，非默认值
- [ ] Postgres HA + WAL 归档已开启
- [ ] MinIO/S3 bucket 版本控制 + Object Lock
- [ ] 所有 8 个 migration 已在 staging 通过 & 验证
- [ ] `alembic current` 与 spec 版本一致
- [ ] Prometheus 抓到 api + worker 的 `/metrics`
- [ ] Grafana 5 张面板全部有数据
- [ ] 告警规则加载 + 通道（PagerDuty/飞书）已测试
- [ ] 所有 adapter 的 `license_tag` 合规审核通过
- [ ] `RUN_LIVE_ADAPTERS=1` 的 CI job 全绿
- [ ] `docker compose down && up` 冒烟测试通过
- [ ] Runbook（本文档 §10 / §11）打印或加入 on-call wiki
- [ ] Rollback 演练过 ≥ 1 次

---

## 13. 变更管理

- 任何 migration 上生产前必须走双人 review（`packages.audit` 上会记 `SchemaChange` 事件）。
- 任何 `PRODUCTION` 模型切换必须走 `apps/quant-admin-mcp` 的双审批流程 + §81.1 gate 证据。
- `deploy/docker-compose.yml` 的镜像 tag 变更必须绑定 spec 修订号。

**禁止事项**：
- 直连生产 Postgres 手改数据。
- 用 dev 的 `INTERNAL_RESEARCH` 数据训练然后升 PRODUCTION（licensing 违规）。
- 关闭 audit 采集（哪怕临时）。
- 通过 SSH 手动改容器状态而不留 audit 事件。
