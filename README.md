# PHP 代码诊断 RAgent

当前项目已完成阶段 0 至阶段 6，正在推进阶段 7：安全、性能和生产上线。

## 本地启动

1. 复制 `.env.example` 为 `.env`。
2. 创建 Python 虚拟环境：`python3 -m venv .venv`。
3. 升级 pip：`.venv/bin/python -m pip install --upgrade pip`。
4. 安装开发依赖：`.venv/bin/pip install '.[dev]'`。
5. 启动 API：`.venv/bin/uvicorn app.main:app --reload`。

## Docker 启动

复制 `.env.example` 为 `.env`，然后执行 `docker compose up --build`。

API 默认运行在 http://127.0.0.1:8000，Worker 使用 Celery 连接 Redis。

健康检查分为：

```text
GET /health       详细依赖状态，用于人工排查
GET /health/live  进程存活检查，不访问外部依赖
GET /health/ready 流量就绪检查，PostgreSQL 或 Redis 异常时返回 503
```

Compose 的 API 健康检查使用 `/health/ready`，依赖不可用时容器会标记为 unhealthy。

诊断任务和文档索引任务使用独立队列：`worker` 消费 `diagnosis`，`index-worker` 消费 `index`，
两者可以分别扩缩容。诊断默认软超时 240 秒、硬超时 300 秒；索引默认软超时 900 秒、硬超时
960 秒，可通过 `DIAGNOSIS_TASK_*_TIME_LIMIT_SECONDS` 和
`DOCUMENT_INDEX_*_TIME_LIMIT_SECONDS` 调整。软超时会写入失败状态和超时原因；硬超时或 Worker
中断后，超过硬超时窗口仍停留在执行阶段的任务会在数据库初始化时恢复为失败。

历史数据保留期限默认：终态诊断任务 90 天、文档索引运行记录 90 天、验证审计 180 天、
安全审计 180 天。可通过 `.env` 中对应的 `*_RETENTION_DAYS` 调整。清理脚本默认只试运行：

```bash
docker compose run --rm api python scripts/cleanup_retention.py
docker compose run --rm api python scripts/cleanup_retention.py \
  --batch-size 500 --max-batches 100
```

确认候选数量无误后必须显式增加 `--apply` 才会删除；删除终态任务时，关联证据、反馈和任务操作
会由外键级联清理。脚本按小批次执行，超过 `--max-batches` 未完成时返回非零状态，便于定时任务告警。

## 上线预检

在容器网络内执行配置和依赖预检：

```bash
docker compose run --rm api python scripts/preflight_check.py
```

生产发布门禁必须使用严格模式：

```bash
docker compose run --rm api python scripts/preflight_check.py --strict
```

预检会检查生产环境、API Key 认证、任务超时、数据保留策略、独立只读数据库、代码仓库目录、
RAgent 元数据库核心表和 Redis。`--strict` 模式下任何 `WARN` 或 `FAIL` 都返回非零状态。
完整部署、告警、备份和回滚要求见 `PRODUCTION_CHECKLIST.md`。

`APP_ENV=production` 时，API 启动阶段也会执行关键静态配置门禁；认证、目标只读库、超时、保留策略
或仓库目录不合格时直接拒绝启动，避免服务带病接收流量。

## 备份和恢复

通过维护 profile 创建 RAgent 元数据库备份：

```bash
docker compose --profile maintenance run --rm backup
```

备份使用 PostgreSQL custom format，生成 SHA-256 校验文件，并通过归档目录自检；默认保存在被 Git
忽略的 `backups/` 目录并保留最近 7 份。生产环境必须将备份复制到加密、受访问控制且与运行环境隔离的
对象存储，按周期执行隔离数据库恢复演练。详细步骤和禁止事项见 `BACKUP_RESTORE.md`。

启动后访问 `http://127.0.0.1:8000/ui` 可打开轻量前端示例，页面支持创建诊断任务、查看 SSE
进度、预览或下载 Markdown 报告，以及提交人工反馈。该页面使用原生 HTML、CSS 和 JavaScript，
不需要额外的前端构建工具。

## API 访问控制

开发环境默认关闭 API Key 认证，保持本地调试兼容。生产环境应设置：

可直接参考 `.env.production.example`。该文件只包含占位值，真实密码和 API Key 必须由 Secret Manager 注入。

```bash
export AUTH_ENABLED=true
export API_KEYS_JSON='{"replace-with-long-random-key":{"subject":"developer","projects":["waky3"],"scopes":["read","write"]}}'
```

也可把同样配置写入 `.env`。API Key 至少 16 个字符，`projects` 使用项目白名单或 `["*"]`，
`scopes` 支持 `read` 和 `write`；GET 请求需要 `read`，POST 请求需要 `write`。

调用 API 时使用请求头：

```text
X-API-Key: replace-with-long-random-key
```

或 `Authorization: Bearer <API Key>`。`/`、`/health`、`/ui`、`/docs` 和 `/openapi.json` 保持公开；
其余 `/api/` 路径均受保护。启用认证后，历史查询和数据库验证必须指定项目，任务、代码、检索、
索引和反馈接口会校验任务或路径所属项目。前端页面的 API Key 输入框只保存在当前页面内存中，
不会写入浏览器存储。

认证失败和项目越权会写入 `security_audit`，记录事件类型、HTTP 状态、方法、路径、来源地址、
认证主体和拒绝原因。审计不保存请求头和原始 API Key；数据库审计写入失败不会改变 401/403 安全拒绝。

所有 API 写请求默认限制为 1MB，可通过 `MAX_REQUEST_BODY_BYTES` 调整。限制在认证和 JSON 解析前执行；
有 `Content-Length` 时提前拒绝，无长度时按流读取并在超限时停止，超限返回 HTTP 413。

响应默认包含 `nosniff`、`DENY` frame、Referrer Policy、CSP 和 Permissions Policy；经 HTTPS 代理访问时
添加 HSTS。跨域默认关闭，可通过逗号分隔的 `CORS_ALLOWED_ORIGINS` 配置白名单，允许使用
`Authorization`、`X-API-Key` 和报告下载响应头。

每个请求会生成或校验 `X-Request-ID` 并通过响应头返回。访问日志以 JSON 记录请求 ID、方法、路径、
状态码和耗时，不记录查询参数、请求体或认证头。

## 监控指标

`GET /api/metrics` 以 Prometheus 文本格式输出 HTTP 请求量、延迟直方图、并发请求、进程运行时长、
任务状态、任务操作、安全拒绝和数据库指标采集状态。启用 API 认证时，抓取端需提供具备 `read` scope
的 API Key：

```yaml
scrape_configs:
  - job_name: php-diagnostic-ragent
    metrics_path: /api/metrics
    authorization:
      type: Bearer
      credentials: replace-with-long-random-key
    static_configs:
      - targets: ["127.0.0.1:8000"]
```

HTTP 路径使用路由模板标签，任务 ID 等动态值不会被写入指标。数据库指标查询使用 3 秒超时；采集失败时
仍返回 HTTP 指标，并输出 `ragent_database_up 0` 和累计采集失败次数。

Prometheus 告警规则位于 `monitoring/prometheus-rules.yml`，覆盖数据库指标不可用、HTTP 5xx 比例、
P95 延迟、并发请求、安全拒绝、任务重试、失败任务和指标采集异常。可在 Prometheus 中通过
`rule_files` 加载该文件，再由 Alertmanager 负责通知路由。

执行开发环境性能冒烟基线：

```bash
RAGENT_API_KEY='replace-with-long-random-key' \
  .venv/bin/python scripts/performance_baseline.py \
  --base-url http://127.0.0.1:8000 \
  --path /api/metrics \
  --requests 100 \
  --concurrency 10 \
  --max-error-rate 0.01 \
  --max-p95-ms 500
```

脚本输出 P50/P95/P99、平均/最大延迟、错误率、吞吐和状态码分布；超过错误率或 P95 阈值时以非零状态退出。
API Key 仅从 `RAGENT_API_KEY` 环境变量读取，避免出现在命令行参数和终端历史中。

## 测试

执行 `.venv/bin/pytest -q`。

GitHub Actions 的 `CI` 工作流在 Python 3.9 和 3.11 下执行单元测试、Python 编译、前端脚本语法、
备份脚本语法、Compose 普通/维护配置和空白错误检查。推送 `main` 或创建 Pull Request 时自动运行。

运行离线诊断准确性基线：

```bash
.venv/bin/python scripts/evaluate_diagnosis.py
```

固定评测集位于 `tests/fixtures/diagnosis_cases.json`，检查符号、证据文件、搜索关键词和关键行号，
同时包含一个不应产生证据的负例。可用 `--min-pass-rate` 设置发布门禁阈值。

从历史正确反馈导出评测候选：

```bash
docker compose run --rm api python scripts/export_evaluation_candidates.py --dry-run
docker compose run --rm api python scripts/export_evaluation_candidates.py \
  --project waky3 --days 180 --output /tmp/diagnosis-candidates.json
```

历史反馈仅生成 `review_status=pending` 的候选，不能直接作为准确率真值。人工核对问题、符号、文件和行号
后再合并到固定评测集。

## 代码注释约定

所有新增或修改的代码文件，都必须同步维护注释：

- 模块顶部说明模块职责；
- 类注释说明类的职责和边界；
- 方法或函数注释说明用途、关键参数、返回值和异常边界；
- 复杂流程保留必要的行内注释，并在行为变化时同步更新；
- 注释使用中文，技术名词和接口名称保留原文。

## Git 提交约定

- 每完成一个阶段性事项，先完成测试、验证和 `progress.md` 更新；
- 默认不主动执行 Git commit，先等待用户审阅；
- 只有用户明确确认“可以提交”或表达等价意图后，才创建中文 commit；
- commit 说明必须使用中文，明确记录本次推进或修改内容；
- 未经确认的改动保持在工作区，不能为了提交而覆盖或丢弃。

## 当前阶段说明

当前已支持：

- 创建、查询、取消诊断任务；
- Celery/Redis 异步执行；
- SSE 进度推送；
- 代码关键词检索；
- PHP/配置文件按行读取；
- 代码仓库和目录白名单校验。

代码分析只读接口：

```text
GET /api/code/{project}/symbols?name=Class::method
GET /api/code/{project}/references?symbol=Class::method
GET /api/code/{project}/call-chain?entry=Class::method&depth=5
```

只读验证接口：

```text
POST /api/validation/database/query
POST /api/validation/config/read
POST /api/validation/logs/search
```

阶段 5 混合检索接口：

```text
GET /api/search/{project}/hybrid?query=关键词&limit=20&history_offset=0&history_days=365&source_types=code&source_types=symbol
```

当前版本合并代码文本、PHP 符号和已完成诊断问题三类结果，统一返回来源类型、
来源标识、原始分数和加权分数；支持来源类型筛选、历史结果偏移量和时间范围筛选。
历史结果只返回脱敏摘要，不能直接替代当前代码和事实证据。

阶段 5 已建立 `document_chunks` 统一分块表，保存来源类型、稳定来源标识、文件行号、
内容哈希、MVP 文本向量和 PostgreSQL `tsvector` 全文索引，为后续替换真实 embedding
服务提供基础。当前向量由确定性哈希特征生成，不依赖外部模型；每个分块同时记录
embedding 模型标识和维度，生产环境替换向量提供商时可避免新旧向量混用。
召回时如果发现模型或维度不兼容，会自动关闭向量分数并降级使用全文分数，同时在结果
中标记 `vector_compatible=false`。
向量生成通过 `EmbeddingProvider` 契约隔离，当前默认实现为 `DeterministicEmbeddingProvider`；
接入真实模型时只需实现同一 `embed(text)` 接口并更新模型标识。

建立项目分块索引：

```bash
.venv/bin/python scripts/index_document_repository.py waky3
.venv/bin/python scripts/index_document_repository.py waky3 --dry-run
```

正式索引会在 `document_index_runs` 中记录 `RUNNING`、`COMPLETED` 或 `FAILED` 状态、
分块数量、仓库提交 SHA、索引版本和错误信息，便于排查失败并重新执行。
开始新索引前会自动恢复同项目超过 60 分钟仍处于 `RUNNING` 的历史记录，标记为失败并
保留超时原因，避免悬挂运行长期占用状态。

查询索引运行记录：

```text
GET /api/index/{project}/runs?limit=20
```

失败索引可通过以下接口异步重试：

```text
POST /api/index/{project}/runs/{run_id}/retry
```

验证接口支持可选 `task_id` 关联诊断任务。每次成功或失败操作都会写入
`validation_audit` 审计表；审计仅保存文件名、行数、匹配数等摘要，并对敏感键值
递归脱敏，不保存完整密码、Token、Cookie、SQL 参数或配置内容。审计写入失败不会
覆盖原始验证结果。

数据库接口只允许 `SELECT` 或只读 `WITH` 查询，并限制参数数量、返回行数、结果大小和查询时间；数据库结果、配置与日志响应会对常见 Token、密码和 Secret 做脱敏。

目标业务数据库必须通过 `VALIDATION_DATABASE_URL` 单独配置，不能复用 RAgent 元数据库 `DATABASE_URL`。
生产环境应使用独立只读账号，并限制其只能访问允许的业务库和 schema。即使账号权限配置错误，每次查询仍会在
PostgreSQL `readonly=True` 事务中执行。未配置 `VALIDATION_DATABASE_URL` 时，数据库验证会返回受控失败，
不会回退到 RAgent 主库。

验证接口默认按调用方进行每分钟 60 次限流，可通过
`VALIDATION_RATE_LIMIT_PER_MINUTE` 和 `VALIDATION_RATE_LIMIT_WINDOW_SECONDS` 调整。
限流计数使用 Redis Lua 原子脚本在多个 API 实例间共享；启用认证时按认证主体计数，否则按客户端地址计数。
Redis 不可用时验证接口会安全返回 503，不会降级为无限制访问。限流拒绝仍返回 429 和 `Retry-After`。

建立 PHP 符号索引：

```bash
.venv/bin/python scripts/index_php_repository.py waky3
```

只扫描并统计符号、不连接数据库：

```bash
.venv/bin/python scripts/index_php_repository.py waky3 --dry-run
```

当前诊断报告会汇总关键词检索、PHP 符号和调用链、按需数据库/配置/日志验证结果；仍未接入
LLM 生成式结论，报告中的事实和建议应以代码证据及验证结果为准。

查询诊断报告并导出 Markdown：

```text
GET /api/diagnoses/{task_id}/report
GET /api/diagnoses/{task_id}/report?format=markdown
```

默认 JSON 响应与任务查询保持一致；Markdown 响应带有下载文件名，包含问题、状态、检索关键词、
代码证据、调用链、事实验证和警告等章节。

报告会基于证据和验证状态生成可解释的“结论、置信度、已确认事实、未验证项”。只有同时存在代码证据、
成功事实验证且没有警告时才标记为高置信度；部分证据标记为中置信度；缺少证据或验证失败标记为低置信度。
该判断不替代人工确认，也不会把检索结果直接表述为已证实根因。
报告同时展示符号、代码、调用链、配置和 SQL 线索，并根据已获得证据生成只读排查建议。

提交和查询诊断报告人工反馈：

```text
POST /api/diagnoses/{task_id}/feedback
GET /api/diagnoses/{task_id}/feedback?limit=50
GET /api/diagnoses/{task_id}/feedback/summary
GET /api/feedback/{project}/summary
```

反馈类型为 `correct`、`inaccurate`、`insufficient_evidence` 或 `escalate`，可附带不超过
2000 字的说明和提交者标识；反馈会与诊断任务级联保存，任务删除时自动清理。`summary` 接口返回
总反馈数、各类型数量和最近反馈时间。
项目级汇总接口还返回产生反馈的任务数，可用于质量看板和后续反馈趋势分析。

浏览历史诊断任务：

```text
GET /api/diagnoses/history?project=waky3&status=COMPLETED&query=邀请&days=30&offset=0&limit=20
```

历史接口按创建时间倒序返回任务摘要，支持项目、状态、问题关键词、最近天数和分页筛选；前端
页面的“历史任务”区域可直接加载当前项目最近任务，点击任务即可查看报告和反馈，并可继续加载下一页。
页面支持按状态和最近天数筛选，并同时展示当前项目反馈质量摘要。

失败任务可在前端点击“重新执行失败任务”，或调用：

```text
POST /api/diagnoses/{task_id}/retry
```

仅 `FAILED` 状态允许重新执行，每个任务最多人工重试 3 次。重新执行会清理旧证据，队列投递失败时
任务会恢复为失败状态并返回受控错误。

前端任务操作区还支持取消未结束任务和复制当前 Markdown 报告；剪贴板权限不可用时会提示手动复制。

查询任务操作审计轨迹：

```text
GET /api/diagnoses/{task_id}/operations?limit=100
```

审计记录包含操作类型、状态变化、操作方和结构化摘要；审计写入失败不会阻断创建、取消或重试主流程。
前端的“操作轨迹”区域会在任务进入终态、从历史任务打开或执行取消/重试后同步展示审计记录。

创建诊断任务时可以通过 `validation` 提交按需验证计划。Worker 会在代码检索后执行
只读数据库查询、配置读取和日志检索，并将每个步骤的成功、失败或未请求状态写入
诊断报告；单个验证失败不会阻断其他验证步骤。例如：

```json
{
  "question": "检查公会邀请失败原因",
  "project": "waky3",
  "validation": {
    "database": {
      "sql": "SELECT id, status FROM guild WHERE id = $1",
      "params": [1001],
      "max_rows": 10
    },
    "configs": [{"project": "waky3", "file": "config/app.php"}],
    "logs": [{"project": "waky3", "file": "storage/app.log", "query": "invite"}]
  }
}
```

代码仓库以只读方式挂载到 `repositories/<project>`，例如：

```text
repositories/waky3/php/action/BDCenterControl.php
```
