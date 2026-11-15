# RAgent 生产环境部署前最终检查清单

> 适用版本：`main` 分支 commit `9d73048`（阶段 42 完成，2026-11-15）。
> 使用方式：按 G0 → G8 顺序逐项确认；每项只有拿到命令输出、截图或签字等证据后才能勾选。
> 任一“硬性门禁”（标记 **必须**）未通过时，停止发布并记录阻断原因，不得带病上线。

## 零、仓库内已完成的基线（部署前无需重跑，但发布记录需引用）

| 项目 | 已验证结果 |
| --- | --- |
| 自动化测试 | `414 passed` |
| 迁移 | 26 个连续版本；迁移检查 `pending=0 drifted=0`；全新空库 17 张表齐全 |
| 生产等价预检 | 16/16 PASS（production、认证、独立只读库、Redis、元数据库、核心表、embedding 实调） |
| 烟雾测试 | 4/4 PASS（readiness、认证项目、数据库指标、项目看板） |
| 性能基线 | 100 请求、10 并发、0 错误、P95 47.544 ms |
| 备份恢复演练 | 备份 SHA-256 校验通过；隔离库恢复约 40s；核心表行数与主库一致 |
| 诊断验收 | 24/24 通过（含人工别名 B3） |

以上只证明**代码与制品可部署**，不代表目标生产环境已就绪；以下 G1–G8 仍需在目标环境取得证据。

---

## G0 发布窗口与角色（部署前 1 天）

- [ ] **必须** 发布窗口已确定，包含数据库迁移、应用切换、验收和观察期。
- [ ] **必须** 已指定发布人、验证人、回滚决策人、值班人，并确认联系方式可用。
- [ ] **必须** 已确认本次变更范围（阶段 41 人工别名表、阶段 42 预检核心表扩展）和业务影响。
- [ ] 已通知业务方发布期间可能出现的短时只读或诊断排队。
- [ ] 已建立发布记录文档，字段按 `RELEASE.md` 第 7 节模板准备。

## G1 代码与制品（部署前）

- [ ] **必须** `main` 分支 CI 全部通过，本次发布提交已合并且无未评审改动。
- [ ] **必须** 镜像使用不可变 tag 或 `registry/image@sha256:<digest>`，禁止 `latest`。
- [ ] **必须** 镜像内运行用户 UID 为 `10001`：

```bash
docker run --rm "${RAGENT_IMAGE}" id -u
# 预期：10001
```

- [ ] **必须** 镜像 digest、Git commit、SPDX SBOM 和 Trivy 报告已归档，CRITICAL/HIGH 可修复漏洞门禁通过。
- [ ] 已确认镜像构建包含 `migrations/026_create_project_activity_aliases.sql`：

```bash
docker run --rm --entrypoint sh "${RAGENT_IMAGE}" \
  -c 'ls /app/migrations | tail -3'
# 预期包含 026_create_project_activity_aliases.sql
```

## G2 Secret 与配置（部署前，硬性门禁）

按 `.env.production.example` 准备生产 Secret 文件（权限 `0400`，不得提交仓库）：

- [ ] **必须** `APP_ENV=production`、`AUTH_ENABLED=true`。
- [ ] **必须** `API_KEYS_JSON` 使用 Secret Manager 生成的随机 Key，按最小项目与 scope 授权。
- [ ] **必须** `DATABASE_URL`、`VALIDATION_DATABASE_URL`、Redis 地址均由 Secret Manager 注入。
- [ ] **必须** `VALIDATION_DATABASE_URL` 使用独立只读账号，并确认只读：

```bash
psql "${VALIDATION_DATABASE_URL}" -c 'SHOW transaction_read_only;'
# 预期：on
```

- [ ] **必须** 所有曾出现在本地终端、CI 日志或诊断输出中的凭据已轮换，不复用旧值。
- [ ] **必须** `CORS_ALLOWED_ORIGINS` 只包含受控生产前端域名。
- [ ] **必须** 生产 embedding 凭据与配额可用（`EMBEDDING_ENABLED=true` 时）。
- [ ] `LLM_ENABLED` 与发布计划一致；如开启，模型数据策略、费用和超时已确认。
- [ ] `RAGENT_IMAGE`、`RAGENT_REPOSITORIES_ROOT`、`API_BIND_ADDRESS`、`API_PORT` 已写入生产环境。
- [ ] 已确认软超时小于硬超时（诊断 240 < 300；索引 900 < 960）。

## G3 仓库挂载与运行权限（部署前）

- [ ] **必须** 被诊断仓库以只读卷挂载到 `${RAGENT_REPOSITORIES_ROOT}:/repositories:ro`。
- [ ] **必须** 运行用户对业务仓库无写权限，抽查写操作应失败。
- [ ] 生产编排保持只读根文件系统、`cap_drop: ALL`、`no-new-privileges`、非 root UID 和日志轮转。
- [ ] 仓库目录包含目标项目（如 `waky3`），且索引所需文件可读。
- [ ] 已知异常挂载或缺失项目会自动降级而不是导致 API 启动失败。

## G4 数据库备份与迁移（发布开始前，硬性门禁）

- [ ] **必须** 发布前完成一次 PostgreSQL 备份，记录文件路径、大小和 SHA-256。
- [ ] **必须** 备份包含 026 之前的全部表；如使用旧备份恢复，必须重新执行 026 迁移。
- [ ] **必须** 最近一次恢复演练在 30 天内，记录恢复耗时与核心表行数。
- [ ] **必须** 迁移只检查通过：

```bash
docker run --rm --env-file /secure/ragent.production.env "${RAGENT_IMAGE}" \
  python scripts/migrate_database.py --check
# 预期：pending=0 drifted=0
```

- [ ] **必须** 已确认 026 将在生产创建 `project_activity_aliases`，且表、唯一约束、状态约束和索引齐全。
- [ ] 已确认迁移失败时保持旧应用版本，不删除迁移记录、不重置数据库、不回滚已完成 DDL。

执行迁移：

```bash
docker run --rm --env-file /secure/ragent.production.env "${RAGENT_IMAGE}" \
  python scripts/migrate_database.py
docker run --rm --env-file /secure/ragent.production.env "${RAGENT_IMAGE}" \
  python scripts/migrate_database.py --check
# 两次 --check 均须 pending=0 drifted=0
```

- [ ] **必须** 迁移后验证 17 张表存在，重点抽查：

```sql
SELECT COUNT(*) FROM schema_migrations;                    -- 预期 26
SELECT to_regclass('public.project_activity_aliases');      -- 非空
SELECT to_regclass('public.diagnosis_trace_spans');         -- 非空
SELECT to_regclass('public.project_glossary');              -- 非空
```

## G5 启动编排（部署时）

- [ ] **必须** 生产编排配置可解析：

```bash
docker compose -f docker-compose.production.yml config -q
```

- [ ] **必须** 使用生产 Secret 启动，API 不直接暴露公网，由受控 TLS 反向代理转发：

```bash
export RAGENT_IMAGE="registry.example.com/ragent@sha256:<digest>"
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml ps
```

- [ ] **必须** `api`、`worker`、`index-worker` 三个容器均为 `healthy`，不是仅 `Up`。
- [ ] **必须** 启动日志无认证关闭、Secret 缺失、迁移漂移或预检失败告警。
- [ ] 确认 Worker 只消费 `diagnosis` 队列，`index-worker` 只消费 `index` 队列。

## G6 发布后功能验收（启动后 30 分钟内）

- [ ] **必须** readiness 返回 200：

```bash
curl -fsS https://<生产域名>/health/ready
```

- [ ] **必须** 认证烟雾测试通过：

```bash
export RAGENT_API_KEY="$(secret-manager read ragent-smoke-key)"
export RAGENT_BASE_URL="https://<生产域名>"
export RAGENT_SMOKE_PROJECT="waky3"
python scripts/smoke_test.py --require-api --json
# 预期 4 项 PASS
```

- [ ] **必须** 性能基线通过：

```bash
python scripts/performance_baseline.py \
  --base-url "${RAGENT_BASE_URL}" --path /health/ready \
  --requests 100 --concurrency 10 --max-error-rate 0.01 --max-p95-ms 500
```

- [ ] **必须** 提交 1 个受控测试诊断任务，状态到达 `COMPLETED`，SSE 与报告可查看。
- [ ] **必须** 失败任务重试、批量重试和取消操作可执行，审计可查询。
- [ ] 如业务依赖人工别名（例如 `鉴定大师 → gemMaster`）：
  - [ ] 已在生产通过 `/api/knowledge/waky3/activity-aliases` 或前端登记；
  - [ ] 已用该叫法提交诊断并确认 `retrieval_plan.activity_link.feedback_alias` 命中；
  - [ ] 已确认别名不会随迁移或代码自动带入，需业务方确认目标活动后再登记。
- [ ] 抽查 `/api/metrics` 中诊断任务、失败率、队列深度和 embedding 指标有数据。
- [ ] 安全审计、限流和 LLM 开关状态与发布记录一致。

## G7 监控与告警（启动后 1 小时内）

- [ ] **必须** Prometheus 已抓取 `/api/metrics`，target 为 `UP`。
- [ ] **必须** `monitoring/prometheus-rules.yml` 已加载，无语法错误。
- [ ] **必须** Alertmanager 通知路由、值班人和升级策略已配置并发送测试告警成功。
- [ ] 已确认 API 5xx、任务失败率、队列积压、LLM 熔断、embedding 失败和磁盘水位告警阈值。
- [ ] 日志轮转生效（单文件 20m、保留 5 个），无 Secret 或完整问题文本泄漏。

## G8 回滚预案与收尾

- [ ] **必须** 上一个已验证镜像 digest 已记录，可一键回退：

```bash
export RAGENT_IMAGE="registry.example.com/ragent@sha256:<上一个digest>"
docker compose -f docker-compose.production.yml up -d
```

- [ ] **必须** 回滚决策人已知晓：应用回滚不等于数据库回滚，优先前向兼容修复。
- [ ] **必须** 如需数据恢复，只能在隔离库恢复并验证后再切换流量，禁止直接覆盖生产库。
- [ ] 发布记录已填写：版本/commit、digest、迁移版本与 checksum、备份文件与校验值、起止时间、人员、CI/SBOM/Trivy、验收结果、受控任务 ID、异常处置。
- [ ] 观察期结束前保留现场日志、请求 ID、失败快照和监控截图。

---

## 签核表

| 角色 | 姓名 | 确认内容 | 时间 |
| --- | --- | --- | --- |
| 发布人 |  | 制品、迁移、编排已按清单执行 |  |
| 验证人 |  | G5–G7 证据已核对 |  |
| 回滚决策人 |  | 回滚镜像与数据恢复预案已确认 |  |
| 业务负责人 |  | 变更窗口、别名数据和业务验收已确认 |  |
| 值班人 |  | 监控、告警和升级路径已确认 |  |

## 立即停止发布的退出条件

1. 迁移 `--check` 出现 `pending` 或 `drifted`，或 026 在生产库创建失败。
2. 生产 Secret 缺失、认证未启用、只读验证库不是只读。
3. 预检、readiness、烟雾测试或性能基线任一硬性门禁失败。
4. 生产编排容器不是 `healthy`，或 Worker 消费了错误队列。
5. 备份未完成或恢复演练超过 30 天且无豁免批准。
6. 出现无法在发布窗口内定位的数据一致性或安全告警。

## 证据归档清单

- CI 运行链接、镜像 digest、SBOM、Trivy 报告；
- 迁移 `--check` 与应用迁移输出、`schema_migrations` 行数；
- 备份文件 SHA-256、恢复演练记录；
- 预检 16 项、烟雾测试 4 项、性能基线输出；
- 受控诊断任务 ID、SSE/报告截图、别名登记与命中记录；
- Prometheus target、告警测试通知截图；
- 回滚镜像 digest 和最终签字表。
