# PHP 代码诊断 RAgent

当前项目已完成阶段 0 至阶段 3，正在推进阶段 4：数据库、配置和日志验证。

## 本地启动

1. 复制 `.env.example` 为 `.env`。
2. 创建 Python 虚拟环境：`python3 -m venv .venv`。
3. 升级 pip：`.venv/bin/python -m pip install --upgrade pip`。
4. 安装开发依赖：`.venv/bin/pip install '.[dev]'`。
5. 启动 API：`.venv/bin/uvicorn app.main:app --reload`。

## Docker 启动

复制 `.env.example` 为 `.env`，然后执行 `docker compose up --build`。

API 默认运行在 http://127.0.0.1:8000，Worker 使用 Celery 连接 Redis。

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

验证接口支持可选 `task_id` 关联诊断任务。每次成功或失败操作都会写入
`validation_audit` 审计表；审计仅保存文件名、行数、匹配数等摘要，并对敏感键值
递归脱敏，不保存完整密码、Token、Cookie、SQL 参数或配置内容。审计写入失败不会
覆盖原始验证结果。

数据库接口只允许 `SELECT` 或只读 `WITH` 查询，并限制参数数量、返回行数、结果大小和查询时间；数据库结果、配置与日志响应会对常见 Token、密码和 Secret 做脱敏。

建立 PHP 符号索引：

```bash
.venv/bin/python scripts/index_php_repository.py waky3
```

只扫描并统计符号、不连接数据库：

```bash
.venv/bin/python scripts/index_php_repository.py waky3 --dry-run
```

当前的诊断报告仍是关键词检索报告，尚未接入 LLM、PHP AST 调用链分析、数据库验证和日志验证。

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
