# PHP 代码诊断 RAgent

当前项目已完成阶段 0 至阶段 5，正在推进阶段 6：前端和协作能力。

## 本地启动

1. 复制 `.env.example` 为 `.env`。
2. 创建 Python 虚拟环境：`python3 -m venv .venv`。
3. 升级 pip：`.venv/bin/python -m pip install --upgrade pip`。
4. 安装开发依赖：`.venv/bin/pip install '.[dev]'`。
5. 启动 API：`.venv/bin/uvicorn app.main:app --reload`。

## Docker 启动

复制 `.env.example` 为 `.env`，然后执行 `docker compose up --build`。

API 默认运行在 http://127.0.0.1:8000，Worker 使用 Celery 连接 Redis。

启动后访问 `http://127.0.0.1:8000/ui` 可打开轻量前端示例，页面支持创建诊断任务、查看 SSE
进度、预览或下载 Markdown 报告，以及提交人工反馈。该页面使用原生 HTML、CSS 和 JavaScript，
不需要额外的前端构建工具。

## 测试

执行 `.venv/bin/pytest -q`。

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

验证接口默认按调用方进行每分钟 60 次限流，可通过
`VALIDATION_RATE_LIMIT_PER_MINUTE` 和 `VALIDATION_RATE_LIMIT_WINDOW_SECONDS` 调整。
当前限流器是单进程实现；生产多实例部署应替换为 Redis 等共享计数器。

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
