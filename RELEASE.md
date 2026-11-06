# RAgent 生产发布与回滚手册

本手册定义从已合并提交到生产验收的可重复流程。生产数据库、Redis、只读业务库、TLS 入口、
Secret Manager、Prometheus/Alertmanager 和对象存储备份属于部署环境资源，必须由对应负责人提供并验证。

## 当前交付状态

仓库内生产交付资产已于 2026-11-06 完成本机生产等价演练：锁定依赖镜像以非 root UID `10001` 启动，
只读根文件系统、`cap_drop: ALL` 和 `no-new-privileges` 下 API 与 Worker 均可运行；全新空库可从 001 一次迁移到 021，
11 张核心表齐全且无漂移；
严格预检、认证烟雾测试、项目看板、数据库指标、诊断任务和性能基线均通过。

这不代表目标生产环境已经上线。正式放量前仍必须由责任人提供并验证真实生产域名与 TLS、Secret Manager、
托管 PostgreSQL/Redis、独立只读业务库、只读代码仓库、Prometheus/Alertmanager、镜像仓库写权限、远端
SBOM/Trivy 结果、隔离恢复演练和上线签字。上述外部项完成前，只能把当前版本定义为“可部署候选版本”。

## 1. 发布前置条件

- `main` 分支 CI 全部通过，镜像由 GitHub Actions 生成并在 `Release gate` 中通过高危漏洞门禁。
- 发布镜像使用 Git commit SHA、语义版本或 `registry/image@sha256:<digest>`，禁止使用 `latest`。
- 生产 Secret 已注入 `API_KEYS_JSON`、`DATABASE_URL`、`VALIDATION_DATABASE_URL`、Redis 地址和可选 LLM 凭据。
- `VALIDATION_DATABASE_URL` 使用独立只读账号，并已确认 `SHOW transaction_read_only` 为 `on`。
- `RAGENT_REPOSITORIES_ROOT` 只读挂载，运行用户没有业务仓库写权限。
- 已完成最近一次 PostgreSQL 备份恢复演练，并记录恢复耗时和核心表行数。
- 值班人、发布人、验证人和回滚决策人已确认。

## 2. 构建与供应链验收

```bash
export RAGENT_VERSION="$(git rev-parse --short=12 HEAD)"
docker build \
  --build-arg RAGENT_REVISION="$(git rev-parse HEAD)" \
  -t "registry.example.com/ragent:${RAGENT_VERSION}" .

docker run --rm "registry.example.com/ragent:${RAGENT_VERSION}" id -u
# 预期输出：10001
```

发布标签 workflow 会保存 SPDX SBOM；Trivy 对 OS 和 Python 依赖中的 CRITICAL/HIGH 可修复漏洞返回非零。
SBOM、漏洞报告、镜像 digest 和 Git commit 必须归档到发布记录。

## 3. 数据库迁移

迁移器按 `NNN_name.sql` 顺序执行，使用 PostgreSQL 事务和咨询锁，并保存 SHA-256 校验和。已发布脚本被修改时
必须阻断发布，不能通过更新校验和绕过。

```bash
# 只检查：存在 pending 或 drifted 时返回非零。
docker run --rm --env-file /secure/ragent.production.env \
  "registry.example.com/ragent:${RAGENT_VERSION}" \
  python scripts/migrate_database.py --check

# 备份成功后应用，全部待执行迁移在同一事务内提交或回滚。
docker run --rm --env-file /secure/ragent.production.env \
  "registry.example.com/ragent:${RAGENT_VERSION}" \
  python scripts/migrate_database.py
```

执行结束后再次运行 `--check`，必须显示 `pending=0 drifted=0`。迁移失败时保持旧应用版本，修复 SQL 或前向兼容问题，
禁止删除迁移记录、重置生产数据库或直接回滚已完成 DDL。

## 4. 启动生产编排

```bash
export RAGENT_IMAGE="registry.example.com/ragent:${RAGENT_VERSION}"
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml ps
```

生产编排不启动 PostgreSQL 和 Redis，使用 Secret Manager 注入的托管服务。容器启用只读根文件系统、
非 root 用户、只读仓库卷、`cap_drop: ALL`、`no-new-privileges` 和日志轮转。

## 5. 发布后验收

```bash
export RAGENT_API_KEY="$(secret-manager read ragent-smoke-key)"
export RAGENT_BASE_URL="https://ragent.example.com"
export RAGENT_SMOKE_PROJECT="waky3"

python scripts/smoke_test.py --require-api --json
python scripts/performance_baseline.py \
  --base-url "${RAGENT_BASE_URL}" --path /health/ready \
  --requests 100 --concurrency 10 --max-error-rate 0.01 --max-p95-ms 500
```

烟雾测试只读检查 readiness、认证项目列表、数据库指标和项目任务看板，不创建诊断任务。还需人工确认：

- 诊断 Worker 和索引 Worker 分别只消费 `diagnosis`、`index` 队列；
- 新建一个受控测试诊断任务可到达 `COMPLETED`，SSE 和报告可查看；
- 失败任务重试、批量重试和取消操作审计可查询；
- `/api/metrics` 已被 Prometheus 抓取，`monitoring/prometheus-rules.yml` 已加载；
- 安全审计、限流、LLM 关闭或启用状态与发布记录一致。

## 6. 回滚

### 应用回滚

1. 将 `RAGENT_IMAGE` 改回上一个已验证 digest。
2. 使用同一生产环境 Secret 执行 `docker compose -f docker-compose.production.yml up -d`。
3. 执行 readiness、烟雾测试和受控诊断验收。
4. 保留失败版本日志、请求 ID 和镜像 digest，不删除现场证据。

### 数据回滚

应用回滚不等于数据库回滚。优先发布前向兼容修复，避免覆盖上线后的新增数据。只有确认数据损坏且获得回滚决策人批准后，
才可在隔离数据库恢复备份；恢复完成后先验证核心表、索引、行数和只读业务隔离，再切换流量。

## 7. 发布记录模板

```text
版本/Commit：
镜像 digest：
迁移版本和 checksum：
数据库备份文件及校验值：
发布开始/结束时间：
发布人 / 验证人 / 回滚决策人：
CI、SBOM、漏洞扫描结果：
readiness、烟雾测试、性能基线：
受控诊断任务 ID：
异常与处置：
回滚演练或实际回滚记录：
```
