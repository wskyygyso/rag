# PHP 代码诊断 RAgent 实现方案

## 1. 文档说明

本文档用于设计一个面向 PHP/Miyou 项目的代码问题诊断 RAgent。

当前项目目录为空，因此本方案按绿地项目设计。第一阶段目标是实现一个类似“首见问题诊断机器人”的异步诊断系统：用户输入业务问题，Agent 自动搜索代码、配置和调用关系，在需要时查询只读数据库或日志，最后输出带证据链的诊断报告。

## 2. 建设目标

系统需要支持：

- 接收自然语言问题；
- 自动提取业务对象、操作和现象；
- 搜索 PHP 代码、配置文件、SQL 和文档；
- 分析类、方法和函数之间的调用关系；
- 通过只读数据库和日志查询验证候选原因；
- 实时返回任务进度；
- 输出包含文件路径、行号和查询条件的诊断报告；
- 保存诊断任务、证据和最终结论；
- 支持失败重试和历史问题检索。

第一版禁止 Agent 修改代码、数据库和线上配置。

## 3. 总体架构

```text
Web / IM / 管理后台
        │
        ▼
Diagnosis API
        │
        ├── 创建诊断任务
        ├── 查询任务状态
        └── SSE 推送进度
        │
        ▼
任务队列 Redis
        │
        ▼
Agent Worker
        │
        ├── 问题理解
        ├── 关键词生成
        ├── 代码检索
        ├── 调用链分析
        ├── 配置检索
        ├── 数据库验证
        ├── 日志验证
        └── 报告生成
        │
        ▼
PostgreSQL / MySQL
代码索引、任务记录、证据、最终报告
```

建议拆分为以下服务：

1. **API 服务**：负责鉴权、创建任务、查询状态和推送进度。
2. **任务编排器**：负责控制 Agent 的状态和执行顺序。
3. **工具服务**：提供代码搜索、文件读取、调用链、数据库和日志查询。
4. **索引服务**：解析 PHP 代码，生成文本、符号和向量索引。
5. **报告服务**：把事实、证据、推测和建议组织为固定格式。

## 4. 推荐技术栈

| 模块 | 技术建议 |
|---|---|
| API | Python FastAPI |
| Agent 编排 | 自定义状态机，或 LangGraph |
| 异步任务 | Celery/RQ + Redis |
| 代码搜索 | ripgrep |
| PHP 解析 | Tree-sitter PHP |
| 调用关系 | Tree-sitter + 静态符号索引 |
| 关键词检索 | PostgreSQL FTS 或 OpenSearch |
| 向量检索 | pgvector |
| 主数据库 | PostgreSQL |
| 前端 | Vue 3 |
| 实时进度 | SSE，后续可扩展 WebSocket |

代码诊断不建议只依赖向量检索，应组合使用：

```text
关键词搜索 + 符号搜索 + 调用链分析 + 向量召回
```

## 5. 诊断任务状态机

```text
CREATED
  ↓
UNDERSTANDING
  ↓
SEARCHING
  ↓
TRACING
  ↓
VERIFYING
  ↓
REPORTING
  ↓
COMPLETED
```

任意状态都可能进入：

```text
FAILED
```

状态职责如下：

### 5.1 UNDERSTANDING

把用户问题解析成结构化对象：

```json
{
  "project": "waky3",
  "domain": "guild",
  "operation": "invite",
  "symptom": "创建超过 3 天的公会无法邀请",
  "entities": ["BD", "guild", "invite"],
  "keywords": [
    "公会邀请",
    "joinBDGuild",
    "BD_GUILD_JOIN_DAY_LIMIT",
    "BD_OP_FORBIDDEN"
  ]
}
```

### 5.2 SEARCHING

按以下顺序检索：

1. 精确关键词；
2. 类名、方法名和配置项；
3. SQL 和错误码；
4. 业务文档和历史问题；
5. 向量检索补充相关上下文。

### 5.3 TRACING

建立从入口到结果的调用链：

```text
请求入口
  ↓
Controller
  ↓
Service / Model
  ↓
配置读取
  ↓
条件判断
  ↓
错误码返回
```

### 5.4 VERIFYING

通过只读工具验证：

- 配置值；
- 数据库记录；
- 日志内容；
- 请求参数；
- 当前时间和业务时间差；
- 客户端和后台是否使用同一接口。

### 5.5 REPORTING

生成固定 JSON 结构，再由前端渲染为聊天消息或诊断报告。

## 6. API 设计

### 6.1 创建诊断任务

```http
POST /api/diagnoses
Content-Type: application/json
```

请求：

```json
{
  "question": "waky3 公会 B 邀请，是邀请不了创建超过 3 天的公会吗？",
  "project": "waky3",
  "environment": "production-readonly"
}
```

响应：

```json
{
  "task_id": "diag_7ed2f15f95b140cb9b9ac6c0bb46d229",
  "status": "QUEUED"
}
```

### 6.2 查询任务

```http
GET /api/diagnoses/{task_id}
```

响应：

```json
{
  "task_id": "diag_xxx",
  "status": "VERIFYING",
  "progress": 72,
  "current_step": "正在查询公会创建时间",
  "result": null
}
```

### 6.3 订阅进度

```http
GET /api/diagnoses/{task_id}/events
Accept: text/event-stream
```

事件：

```json
{
  "status": "SEARCHING",
  "message": "正在检索 BD_GUILD_JOIN_DAY_LIMIT",
  "progress": 42
}
```

### 6.4 取消任务

```http
POST /api/diagnoses/{task_id}/cancel
```

## 7. Agent 工具设计

Agent 不能直接执行任意 Shell 或 SQL，只能调用受控工具。

### 7.1 代码搜索

```json
{
  "name": "search_code",
  "parameters": {
    "query": "BD_GUILD_JOIN_DAY_LIMIT",
    "repository": "waky3",
    "path": "php/",
    "max_results": 20
  }
}
```

返回：

```json
{
  "matches": [
    {
      "file": "php/action/BDCenterControl.php",
      "line": 244,
      "content": "Config::get('anchor.joinBDGuildDayLimit')"
    }
  ]
}
```

### 7.2 文件读取

```json
{
  "name": "read_file",
  "parameters": {
    "file": "php/action/BDCenterControl.php",
    "start_line": 230,
    "end_line": 270
  }
}
```

### 7.3 符号检索

```json
{
  "name": "find_symbol",
  "parameters": {
    "symbol": "BDCenterControl::inviteGuild"
  }
}
```

### 7.4 调用链分析

```json
{
  "name": "trace_call_chain",
  "parameters": {
    "entry": "BDCenterControl::inviteGuild",
    "direction": "both",
    "depth": 5
  }
}
```

### 7.5 只读数据库查询

```json
{
  "name": "query_database",
  "parameters": {
    "database": "waky3",
    "sql": "SELECT gid, regtime FROM guild WHERE gid = :gid",
    "params": {
      "gid": 12345
    },
    "max_rows": 20
  }
}
```

服务端必须强制保证：

- 只允许 `SELECT`；
- 禁止多语句执行；
- 禁止访问系统表；
- 限制执行时间；
- 限制返回行数；
- 敏感字段自动脱敏；
- 所有查询写入审计日志。

## 8. 代码索引方案

```text
扫描 Git 仓库
  ↓
解析 PHP 文件
  ↓
提取类、方法、函数、配置、SQL
  ↓
按语义切分代码片段
  ↓
生成关键词索引和向量
  ↓
写入 PostgreSQL / pgvector
```

建议的代码片段粒度：

- 类；
- 方法；
- 配置数组；
- SQL 查询；
- 注释和业务说明；
- Controller 到 Model 的关联片段。

### 8.1 代码片段表

```sql
CREATE TABLE code_chunks (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    repository VARCHAR(100) NOT NULL,
    file_path TEXT NOT NULL,
    start_line INT NOT NULL,
    end_line INT NOT NULL,
    symbol VARCHAR(255),
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    embedding VECTOR(1536),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

### 8.2 符号表

```sql
CREATE TABLE code_symbols (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    symbol_type VARCHAR(50) NOT NULL,
    symbol_name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    line INT NOT NULL,
    signature TEXT,
    references JSONB
);
```

## 9. 任务和证据表

### 9.1 任务表

```sql
CREATE TABLE diagnostic_tasks (
    id VARCHAR(64) PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    question TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress INT DEFAULT 0,
    current_step VARCHAR(255),
    result JSONB,
    error TEXT,
    created_by VARCHAR(100),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

### 9.2 证据表

```sql
CREATE TABLE diagnostic_evidence (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    evidence_type VARCHAR(30) NOT NULL,
    file_path TEXT,
    line_start INT,
    line_end INT,
    query TEXT,
    content TEXT,
    source JSONB,
    created_at TIMESTAMP NOT NULL
);
```

## 10. 最终报告格式

```json
{
  "question_summary": "waky3 公会 B 邀请是否限制公会创建时间超过 3 天",
  "conclusion": "已确认客户端邀请入口限制公会创建时间不能超过 3 天",
  "confidence": 0.94,
  "confirmed_facts": [
    "客户端邀请入口读取 joinBDGuildDayLimit 配置",
    "当前配置值为 3",
    "邀请前后均进行了公会创建时间校验"
  ],
  "hypotheses": [],
  "evidence": [
    {
      "type": "code",
      "file": "php/action/BDCenterControl.php",
      "line": 244,
      "content": "根据 guild.regtime 判断是否超过限制"
    },
    {
      "type": "config",
      "file": "php/config/anchor.php",
      "line": 39,
      "content": "joinBDGuildDayLimit = 3"
    }
  ],
  "call_chain": [
    "BDCenterControl::inviteGuild",
    "BDCenterControl::checkGuild",
    "Config::get('anchor.joinBDGuildDayLimit')",
    "返回 BD_OP_FORBIDDEN"
  ],
  "suggestions": [
    "后台添加公会应使用后台专用接口",
    "不要复用客户端 BD 邀请接口"
  ],
  "unknowns": [
    "尚未确认后台入口是否使用同一套校验"
  ]
}
```

## 11. 系统 Prompt 约束

```text
你是代码诊断 Agent。

你的任务是根据用户问题定位真实原因，并输出可审计的诊断报告。

必须遵守：
1. 不得仅凭常识下结论。
2. 每个结论必须有证据。
3. 代码证据必须包含文件路径和行号。
4. 数据库证据必须包含查询条件。
5. 区分“已确认”“推测”“未验证”。
6. 不得编造不存在的文件、函数、配置或数据。
7. 证据不足时必须明确说明。
8. 只能通过受控工具访问代码、配置、数据库和日志。
9. 不得修改代码、数据库和线上配置。
10. 优先建立实际调用链，再给出结论。
```

## 12. 推荐项目结构

```text
rag/
├── app/
│   ├── api/
│   │   ├── diagnosis.py
│   │   └── events.py
│   ├── agent/
│   │   ├── orchestrator.py
│   │   ├── state.py
│   │   ├── prompts.py
│   │   └── report.py
│   ├── tools/
│   │   ├── code_search.py
│   │   ├── file_reader.py
│   │   ├── symbol_search.py
│   │   ├── call_graph.py
│   │   ├── database.py
│   │   └── logs.py
│   ├── workers/
│   │   └── diagnosis_worker.py
│   ├── indexer/
│   │   ├── php_parser.py
│   │   ├── chunker.py
│   │   └── indexer.py
│   ├── models/
│   └── settings.py
├── migrations/
├── tests/
├── scripts/
│   ├── index_repository.py
│   └── rebuild_symbols.py
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 13. 分阶段实施计划

### 阶段一：MVP

实现：

- 创建诊断任务；
- Redis 异步执行；
- `search_code`；
- `read_file`；
- 固定格式报告；
- SSE 进度推送。

这一阶段可以暂时不接数据库和向量库，直接使用 `ripgrep`。

### 阶段二：代码理解

增加：

- PHP AST 解析；
- 类和方法索引；
- 函数引用查找；
- 调用链分析；
- 配置项关联；
- SQL 定位。

### 阶段三：事实验证

增加：

- 只读数据库；
- 日志搜索；
- 请求参数回放；
- 环境配置比较；
- 任务证据持久化。

### 阶段四：知识沉淀

增加：

- 历史问题库；
- 故障案例库；
- 业务规则文档；
- 相似问题召回；
- 诊断结果评价和人工反馈。

## 14. 安全设计

必须重点控制以下风险：

1. **数据库越权**：数据库账号只授予只读权限，应用层再次检查 SQL。
2. **敏感信息泄露**：手机号、Token、密码、用户信息和业务密钥统一脱敏。
3. **代码越权访问**：每个项目配置允许访问的仓库和目录白名单。
4. **提示注入**：代码注释、数据库文本和日志内容只能作为数据，不能覆盖系统规则。
5. **无限循环**：限制 Agent 最大步骤数、最大工具调用次数和单次任务预算。
6. **结果误导**：报告必须区分已确认事实、推测和未验证项。
7. **审计缺失**：保存用户问题、工具调用、SQL、返回摘要和最终报告。

## 15. 验收标准

第一版至少满足：

- 输入问题后 2 秒内返回任务 ID；
- 能显示任务阶段和进度；
- 能搜索 PHP 文件并返回准确行号；
- 不引用未检索到的代码；
- 每个结论都有证据；
- 数据库工具无法执行写操作；
- Agent 失败后可以重试；
- 诊断报告可以保存和再次查看；
- 相同问题可以复用历史结果或相关案例。

## 16. 推荐开发顺序

```text
1. 实现任务 API
2. 实现 Redis Worker
3. 接入代码搜索工具
4. 实现 Agent 工具调用循环
5. 输出结构化报告
6. 加入 SSE 进度
7. 接入 PHP 符号分析
8. 接入数据库、日志和向量检索
```

核心原则：

> 模型负责理解问题、规划检索、提出假设和组织报告；
> 代码搜索、静态分析、数据库和日志系统负责提供事实；
> 编排器负责控制过程；
> 报告必须保留完整证据链。
