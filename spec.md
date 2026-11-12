# 阶段 35 规格：自动派生的项目知识库

## 一、目标与边界

### 目标

为每个已索引项目自动派生三类可重建知识，降低“遇到新业务名/新活动就要改代码或加映射”的频率：

1. **项目术语表**：中文业务词 ↔ 真实代码标识（类名、方法名、文件名干、目录名、配置键、运行时模型扩展词）。
2. **配置结构摘要**：由诊断过程中已读入证据窗口的 PHP 配置自动落库，记录文件、数组路径、键名、值类型、分支和脱敏展示值。
3. **已验证结论库**：把已完成诊断的确定性结论带证据引用、按问题哈希与代码指纹保存；反馈为“正确”才升级为 `verified`，只作为检索候选提示，**绝不进入新的 `conclusion`**。

### 硬性边界

- 知识库必须**自动派生、可按代码指纹重建**，不新增人工维护的业务文档。
- 知识库只影响**检索与术语扩展**，不得改变既有确定性结论的生成逻辑。
- 配置结构只保存 `config_graph` 已过滤的安全值；密钥、令牌、URL、路径、邮箱一律不落库明文。
- 任何知识库读写失败都必须静默降级，诊断主流程继续使用现有静态概念与真实源码检索。
- 所有新增/修改代码、迁移、配置必须带中文注释，满足 AGENTS.md 注释覆盖要求。

## 二、现状依据（已确认）

| 项目 | 现状 |
|---|---|
| 工作区 | `main`，最新提交 `9f5b6dc`，无未提交改动 |
| 迁移 | `migrations/001..024` 连续编号，下一个为 `025` |
| 符号索引 | `app/php_index.py::index_repository` 返回 `symbols/references/configs/sql` |
| 索引缓存 | `app/project_index_cache.py` gzip + Redis，`decode_index` 只保留四类键 |
| 索引落库 | `app/task_store.py::replace_symbol_index_if_changed`，按项目咨询锁与指纹去重 |
| 诊断编排 | `app/diagnosis_workflow.py`：约 1939 行加载索引、1967 行术语扩展、2209 行落符号索引、2337 行确定性结论 |
| 术语扩展 | `app/term_expansion.py::expand_question_terms`，模型候选 + 索引真实性校验 + Redis 缓存 v3 |
| 配置图 | `app/domain/diagnosis/config_graph.py::parse_config_graph(lines) -> ConfigGraph`，含 `entries/env_values` |
| 结论路由 | `app/business_analysis.py::build_deterministic_findings`，按 `QuestionType` 分流 |
| API 惯例 | `request.app.state.db_pool`、`require_project_access`、`APIRouter(prefix=...)`、`main.py` 注册 |
| 前端 | 单文件 `static/index.html`（约 2355 行），`apiFetch` 调用 `/api/projects` 等 |
| 反馈 | `diagnostic_feedback` 的 `rating` 含 `correct/inaccurate/insufficient_evidence/escalate` |

## 三、数据模型（`migrations/025_create_project_knowledge.sql`）

### 3.1 `project_glossary`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGSERIAL PK | 主键 |
| `project` | VARCHAR(100) NOT NULL | 项目标识 |
| `term` | VARCHAR(200) NOT NULL | 中文业务词（去空白后的原词） |
| `code_term` | VARCHAR(255) NOT NULL | 真实代码标识或文件名干/目录/配置键 |
| `term_kind` | VARCHAR(32) NOT NULL | `symbol`/`file`/`directory`/`config_key`/`runtime` |
| `source` | VARCHAR(32) NOT NULL | `comment`/`symbol`/`path`/`config_key`/`llm` |
| `evidence_ref` | TEXT | `文件:行号` 或相对路径 |
| `confidence` | NUMERIC(4,3) NOT NULL | 0.000–1.000，注释与符号 0.8，LLM 沉淀 0.5 |
| `repository_fingerprint` | CHAR(64) NOT NULL | 生成该条目的代码快照指纹 |
| `created_at/updated_at` | TIMESTAMPTZ | 审计时间 |

约束：`UNIQUE (project, term, code_term)`；索引 `(project, term)`、`(project, repository_fingerprint)`。

### 3.2 `project_config_structure`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGSERIAL PK | 主键 |
| `project` | VARCHAR(100) NOT NULL | 项目标识 |
| `file` | TEXT NOT NULL | 配置相对路径 |
| `array_path` | TEXT NOT NULL | 数组路径，如 `prize.1` |
| `key_name` | VARCHAR(255) NOT NULL | 键名 |
| `value_kind` | VARCHAR(32) NOT NULL | `numeric`/`bool`/`constant`/`label`/`env_test`/`env_production` |
| `display_value` | TEXT | 已脱敏安全值；敏感/地址项为空 |
| `branch` | VARCHAR(16) | `test`/`production`/空 |
| `line` | INT NOT NULL | 源码行号 |
| `repository_fingerprint` | CHAR(64) NOT NULL | 代码快照指纹 |
| `observed_at` | TIMESTAMPTZ | 最近观测时间 |

约束：`UNIQUE (project, file, array_path, key_name, branch)`；索引 `(project, key_name)`、`(project, file)`。

### 3.3 `project_verified_conclusions`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGSERIAL PK | 主键 |
| `project` | VARCHAR(100) NOT NULL | 项目标识 |
| `question_hash` | CHAR(64) NOT NULL | 规范化问题的 SHA-256 |
| `question` | TEXT NOT NULL | 原始问题 |
| `conclusion` | TEXT NOT NULL | 历史确定性结论 |
| `confirmed_facts` | JSONB NOT NULL DEFAULT '[]' | 事实摘要，不含源码全文 |
| `evidence_refs` | JSONB NOT NULL DEFAULT '[]' | 证据引用 |
| `status` | VARCHAR(16) NOT NULL | `candidate`/`verified`/`expired`/`rejected` |
| `source_task_id` | VARCHAR(64) | 来源任务 |
| `repository_fingerprint` | CHAR(64) NOT NULL | 生成时代码指纹 |
| `verified_at` | TIMESTAMPTZ | 反馈确认时间 |
| `expires_at` | TIMESTAMPTZ | 过期时间，默认 90 天 |
| `created_at/updated_at` | TIMESTAMPTZ | 审计时间 |

约束：`UNIQUE (project, question_hash)`；索引 `(project, status, updated_at DESC)`。

## 四、模块设计

### 4.1 `app/project_knowledge.py`（新建）

职责：知识库抽取、落库、指纹失效、查询、结论候选管理；不访问源码正文之外的仓库文件，不调用模型。

函数清单：

| 函数 | 行为 | 返回/异常 |
|---|---|---|
| `question_hash(question)` | 规范化（小写、去空白）后 SHA-256 | `str` |
| `extract_glossary(index, project)` | 从 `index["glossary"]` 生成 `GlossaryEntry` 列表，去重、截断超长中文词、置信度 0.8 | `list[GlossaryEntry]` |
| `replace_project_glossary_if_changed(pool, project, fingerprint, index)` | 咨询锁内比较 `project_glossary` 现有指纹与预计条数；一致则跳过，否则整项目替换（先删后批量 upsert） | `dict`：`updated/entry_count/previous_count/skipped_reason` |
| `load_glossary_terms(pool, project, question, limit=8)` | 查 `term` 是否为问题子串，按置信度与更新时间排序 | `dict`：`status/terms/entries/reason` |
| `record_runtime_glossary(pool, project, fingerprint, question, terms)` | 把模型扩展词与问题中文锚点配对，`source='llm'`、`term_kind='runtime'`、置信度 0.5，upsert | `int` 写入条数 |
| `record_config_structure(pool, project, fingerprint, entries, env_values)` | 把 `ConfigEntry`/`ConfigEnvValue` 转为结构记录并 upsert；敏感键与地址值只存 `value_kind`，不存明文 | `int` 写入条数 |
| `load_config_structure(pool, project, file="", key="", limit=20)` | 供 API 与检索审计读取结构摘要 | `list[dict]` |
| `save_candidate_conclusion(pool, project, fingerprint, task_id, question, conclusion, facts, evidence_refs)` | 空结论跳过；upsert `candidate`，指纹变化时重置为 `candidate` 并刷新时间 | `bool` |
| `load_conclusion_candidates(pool, project, question, fingerprint, limit=3)` | 只返回 `status in ('candidate','verified')`、未过期、指纹一致的结果 | `list[dict]` |
| `apply_feedback_to_conclusion(pool, project, question, rating)` | `correct` → `verified` 且 `verified_at/expires_at`；`inaccurate` → `rejected`；其余不变 | `str` 新状态或空字符串 |

降级约定：所有 async 函数内部捕获 SQL 异常并返回空结果/0，同时 `logger.warning` 记录 `error_type`，不抛给诊断主流程；仅 `rebuild` 类显式接口允许抛出并转换 503。

### 4.2 `app/php_index.py`（修改）

- `parse_php_source` 新增返回键 `glossary`：识别中文注释与相邻声明/配置键，生成
  `{"term","code_term","term_kind","source","file","line","confidence"}`。
- `index_repository` 的默认结果增加 `"glossary": []`，逐文件合并。
- 判定规则：
  - 类/接口/trait/function 声明前 3 行或同一行的中文注释 → `term_kind=symbol`；
  - 目录名或文件名干必须在同一中文注释块中出现 → `term_kind=file/directory`；
  - `'key' =>` 同行中文注释 → `term_kind=config_key`；
  - 中文词长度 2–40，去掉注释符号、数字标点与空白后仍含中文才保留；
  - 单文件最多 200 条，避免超大注释文件撑爆索引。

### 4.3 `app/project_index_cache.py`（修改）

- `decode_index` 增加可选 `glossary` 列表校验：缺失或类型非列表时视为空列表，其余四类仍严格校验。
- 编码无需改动（`encode_index` 已序列化全部键）。

### 4.4 `app/term_expansion.py`（修改）

- `expand_question_terms` 新增关键字参数 `glossary_terms: Iterable[str] = ()`。
- 流程改为：
  1. 先用 `_validation_tokens(project_index)` 校验知识库词，保留真实词；
  2. 若知识库已验证词非空，返回 `status="glossary_hit"`，不再调用模型；
  3. 否则沿用现有 Redis 缓存与模型流程；
  4. 返回值新增 `glossary_term_count`，`reason` 说明命中来源。
- 兼容性：不改变原有缓存键版本语义，知识库命中不写 Redis。

### 4.5 `app/diagnosis_workflow.py`（修改）

- 加载索引后、术语扩展前：
  - `await load_glossary_terms(pool, project, question, limit=8)`；
  - 将 `terms` 传入 `expand_question_terms(..., glossary_terms=...)`；
  - 知识库查询元数据写入 `expanded_term_summary["glossary"]`。
- 术语扩展产生 `status=="succeeded"` 且有词时，调用 `record_runtime_glossary`。
- `replace_symbol_index_if_changed` 成功后调用 `replace_project_glossary_if_changed`，异常写入 `warnings`。
- 确定性结论生成后：
  - 用证据行构造 `parse_config_graph`，调用 `record_config_structure`；
  - 调用 `save_candidate_conclusion`；
  - `load_conclusion_candidates` 结果只写入 `retrieval_plan["candidate_conclusions"]`，字段含 `not_used_in_conclusion: true`。
- 所有知识库失败只追加 warning，不改变任务状态。

### 4.6 `app/feedback_api.py`（修改）

- 在反馈成功写入后调用 `apply_feedback_to_conclusion`；异常只记录日志，不影响反馈响应。

### 4.7 `app/knowledge_api.py`（新建）

`APIRouter(prefix="/api/knowledge", tags=["knowledge"])`：

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/{project}/summary` | 三类知识的条目数、最近指纹、指纹是否与 `project_symbol_indexes` 一致 |
| GET | `/{project}/glossary` | `term` 模糊过滤、`limit` 1–100 |
| GET | `/{project}/config` | 按 `file`/`key` 过滤、`limit` 1–100 |
| GET | `/{project}/conclusions` | 按 `status` 过滤、`limit` 1–50 |
| POST | `/{project}/rebuild` | 从 `code_symbols` + `project_symbol_indexes` 重建术语表，不扫描仓库 |

- 全部使用 `require_project_access`；数据库缺失返回 503。
- `rebuild` 需要在 `project_knowledge.py` 增加 `rebuild_glossary_from_code_symbols(pool, project)`：按 `project_symbol_indexes.repository_fingerprint` 把 `code_symbols.symbol_name/file_path` 组装成伪索引后复用 `extract_glossary`。

### 4.8 `app/main.py`（修改）

- 导入并注册 `knowledge_router`。

### 4.9 `app/settings.py` + `.env.example`（修改）

- 新增 `project_knowledge_enabled: bool = True`；关闭时 `load_glossary_terms` 直接返回 `disabled`，不写库。
- `.env.example` 增加分组注释与示例值。

### 4.10 `static/index.html`（修改）

- 在项目概览卡片下新增“项目知识库”卡片：
  - 展示术语数、配置结构数、结论数、指纹一致状态；
  - “刷新知识库”按钮调用 `POST /api/knowledge/{project}/rebuild`；
  - 术语输入框按中文词查询，结果展示“中文词 → 代码词（类型/来源）”。

### 4.11 `progress.md`（修改）

- 追加“阶段 35”记录：完成范围、验证结果、剩余项、下一步。

## 五、验收标准

1. 迁移 `025` 可被 `apply_migrations` 顺序应用，三张表、唯一键、索引与中文备注齐全。
2. 索引构建后，`project_glossary` 自动出现注释派生词；同指纹重复运行不重写。
3. 代码指纹变化后，术语表与结论候选自动失效或重建，不返回旧指纹数据。
4. 术语扩展命中知识库时状态为 `glossary_hit` 且不调用模型；未命中时保持原 `succeeded/degraded` 语义。
5. 配置结构只含安全值，敏感键/地址类不出现明文。
6. `conclusion` 与阶段 34 行为一致；候选结论只出现在 `retrieval_plan.candidate_conclusions`。
7. 反馈 `correct` 后对应结论为 `verified`；`inaccurate` 后为 `rejected`。
8. 知识库接口在数据库不可用时返回 503，不导致服务启动失败。
9. 前端知识库卡片可加载摘要、查询术语、触发重建。
10. `pytest` 全量通过（新增 `tests/test_project_knowledge.py`），注释覆盖、前端语法、`git diff --check` 通过。
11. 真实 E2E：waky3 的“游戏榜前 10 名奖励”“VIP 1-12 奖品”“FIFA2026 奖池”“fupay 商户号”结论与阶段 34 一致，且 `retrieval_plan` 出现知识库元数据。

## 六、测试计划

`tests/test_project_knowledge.py`：

- 术语抽取：中文注释 + 类声明、配置键、目录名；长度过滤；单文件上限。
- 指纹失效：同指纹跳过、不同指纹替换、条数变化替换。
- 知识库查询：问题子串命中、无命中、数据库异常降级。
- 术语扩展：`glossary_hit` 跳过模型、知识库词经真实性校验。
- 配置结构：数字/布尔/常量落库，敏感键与 URL 不落明文。
- 结论候选：保存、过期过滤、指纹不一致过滤、反馈状态流转。
- API：summary/glossary/config/conclusions/rebuild，数据库缺失 503。
- 回归：`build_deterministic_findings` 不读取候选结论。

## 七、验证命令

```bash
.venv/bin/python -m pytest -q
python3 scripts/check_comment_coverage.py
node scripts/check_frontend_syntax.js
git diff --check
```

E2E 需要重建镜像后执行：

```bash
docker compose build api worker && docker compose up -d api worker
```

## 八、明确不做

- 不新增人工维护的业务知识文档或映射表。
- 不把知识库结论直接拼进 `conclusion`。
- 不做全仓配置明文落库或密钥缓存。
- 不为单一业务活动新增专属分析器。

## 九、风险与回滚

- 迁移失败：`025` 仅新增表与索引，可直接回滚（删除三表），不影响既有诊断表。
- 知识库写入放大：批量插入受单次 500 条与索引条数上限约束；失败静默降级。
- 指纹误判：复用 `repository_fingerprint`，与现有符号索引保持同一口径。
