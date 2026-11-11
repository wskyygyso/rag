"""诊断任务和证据的 PostgreSQL 持久化操作。"""

import json
import math
from datetime import datetime
from typing import Any, Dict, Optional

import asyncpg

from app.settings import Settings

CREATE_TASKS_SQL = """
CREATE TABLE IF NOT EXISTS diagnostic_tasks (
    id VARCHAR(64) PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    question TEXT NOT NULL,
    environment VARCHAR(100) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress INT NOT NULL DEFAULT 0,
    current_step VARCHAR(255),
    result JSONB,
    error TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    created_by VARCHAR(100),
    validation_plan JSONB,
    use_ai BOOLEAN,
    request_id VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_EVIDENCE_SQL = """
CREATE TABLE IF NOT EXISTS diagnostic_evidence (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    evidence_type VARCHAR(30) NOT NULL,
    project VARCHAR(100),
    file_path TEXT,
    line_start INT,
    line_end INT,
    query TEXT,
    content TEXT,
    source JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_SYMBOLS_SQL = """
CREATE TABLE IF NOT EXISTS code_symbols (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    symbol_type VARCHAR(50) NOT NULL,
    symbol_name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    line INT NOT NULL,
    signature TEXT,
    "references" JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_PROJECT_SYMBOL_INDEXES_SQL = """
CREATE TABLE IF NOT EXISTS project_symbol_indexes (
    project VARCHAR(100) PRIMARY KEY,
    repository_fingerprint CHAR(64) NOT NULL,
    symbol_count INT NOT NULL CHECK (symbol_count >= 0),
    reference_count INT NOT NULL CHECK (reference_count >= 0),
    config_count INT NOT NULL CHECK (config_count >= 0),
    sql_count INT NOT NULL CHECK (sql_count >= 0),
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_VALIDATION_AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS validation_audit (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64),
    operation VARCHAR(50) NOT NULL,
    project VARCHAR(100),
    status VARCHAR(20) NOT NULL,
    request_summary JSONB,
    result_summary JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_VALIDATION_AUDIT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_validation_audit_task_created
    ON validation_audit (task_id, created_at DESC)
"""

CREATE_FEEDBACK_SQL = """
CREATE TABLE IF NOT EXISTS diagnostic_feedback (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    rating VARCHAR(32) NOT NULL CHECK (rating IN ('correct', 'inaccurate', 'insufficient_evidence', 'escalate')),
    comment TEXT,
    submitted_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_FEEDBACK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_diagnostic_feedback_task_created
    ON diagnostic_feedback (task_id, created_at DESC)
"""

CREATE_TASKS_QUALITY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_diagnostic_tasks_project_updated
    ON diagnostic_tasks (project, updated_at DESC)
"""

CREATE_DIAGNOSIS_HISTORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_diagnostic_tasks_project_created
    ON diagnostic_tasks (project, created_at DESC)
"""

CREATE_AI_EVALUATION_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS ai_evaluation_runs (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    min_pass_rate DOUBLE PRECISION NOT NULL,
    total INT NOT NULL,
    executed INT NOT NULL,
    skipped INT NOT NULL,
    passed INT NOT NULL,
    pass_rate DOUBLE PRECISION NOT NULL,
    average_evidence_ref_recall DOUBLE PRECISION NOT NULL,
    result JSONB NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_AI_EVALUATION_RUNS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ai_evaluation_runs_project_created
    ON ai_evaluation_runs (project, created_at DESC)
"""

CREATE_TASK_OPERATIONS_SQL = """
CREATE TABLE IF NOT EXISTS diagnostic_task_operations (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    operation VARCHAR(32) NOT NULL,
    from_status VARCHAR(32),
    to_status VARCHAR(32),
    actor VARCHAR(100),
    request_id VARCHAR(128),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_TASK_OPERATIONS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_task_operations_task_created
    ON diagnostic_task_operations (task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_operations_request_id
    ON diagnostic_task_operations (request_id)
"""

CREATE_SECURITY_AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS security_audit (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(32) NOT NULL,
    status_code SMALLINT NOT NULL,
    reason VARCHAR(500) NOT NULL,
    method VARCHAR(16) NOT NULL,
    path VARCHAR(500) NOT NULL,
    client_address VARCHAR(100),
    actor VARCHAR(100),
    request_id VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_SECURITY_AUDIT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_security_audit_created
    ON security_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_audit_type_created
    ON security_audit (event_type, created_at DESC)
"""

CREATE_DOCUMENT_CHUNKS_SQL = """
CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    source_type VARCHAR(30) NOT NULL,
    source_id VARCHAR(500) NOT NULL,
    file_path TEXT,
    line_start INT,
    line_end INT,
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    embedding JSONB,
    embedding_model VARCHAR(64),
    embedding_dimension SMALLINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project, source_type, source_id)
)
"""

CREATE_DOCUMENT_CHUNKS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_document_chunks_project_type
    ON document_chunks (project, source_type);
CREATE INDEX IF NOT EXISTS idx_document_chunks_search_vector
    ON document_chunks USING GIN (search_vector)
"""

CREATE_DOCUMENT_INDEX_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS document_index_runs (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    repository_revision VARCHAR(128),
    index_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
)
"""

CREATE_DOCUMENT_INDEX_RUNS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_document_index_runs_project_started
    ON document_index_runs (project, started_at DESC)
"""

# 诊断阶段轨迹只保存可审计的阶段、状态、耗时和脱敏统计。问题原文、源码正文、
# 模型请求体、API Key 和数据库连接信息仍保存在各自受控位置，不能写入这里。
CREATE_DIAGNOSIS_TRACE_SPANS_SQL = """
CREATE TABLE IF NOT EXISTS diagnosis_trace_spans (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL REFERENCES diagnostic_tasks(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    elapsed_ms DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_DIAGNOSIS_TRACE_SPANS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_diagnosis_trace_task_created
    ON diagnosis_trace_spans (task_id, id)
"""


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """创建数据库连接池，并确保任务和证据表存在。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
    """
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    async with pool.acquire() as connection:
        await connection.execute(CREATE_TASKS_SQL)
        await connection.execute(CREATE_TASKS_QUALITY_INDEX_SQL)
        await connection.execute(CREATE_DIAGNOSIS_HISTORY_INDEX_SQL)
        await connection.execute(CREATE_AI_EVALUATION_RUNS_SQL)
        await connection.execute(CREATE_AI_EVALUATION_RUNS_INDEX_SQL)
        await connection.execute(
            "ALTER TABLE diagnostic_tasks ADD COLUMN IF NOT EXISTS validation_plan JSONB"
        )
        await connection.execute(
            "ALTER TABLE diagnostic_tasks ADD COLUMN IF NOT EXISTS use_ai BOOLEAN"
        )
        await connection.execute(
            "ALTER TABLE diagnostic_tasks ADD COLUMN IF NOT EXISTS request_id VARCHAR(128)"
        )
        await connection.execute(CREATE_DIAGNOSIS_TRACE_SPANS_SQL)
        await connection.execute(CREATE_DIAGNOSIS_TRACE_SPANS_INDEX_SQL)
        await connection.execute(CREATE_EVIDENCE_SQL)
        await connection.execute(CREATE_SYMBOLS_SQL)
        await connection.execute(CREATE_PROJECT_SYMBOL_INDEXES_SQL)
        await connection.execute(CREATE_VALIDATION_AUDIT_SQL)
        await connection.execute(CREATE_VALIDATION_AUDIT_INDEX_SQL)
        await connection.execute(CREATE_FEEDBACK_SQL)
        await connection.execute(CREATE_FEEDBACK_INDEX_SQL)
        await connection.execute(CREATE_TASK_OPERATIONS_SQL)
        await connection.execute(CREATE_TASK_OPERATIONS_INDEX_SQL)
        await connection.execute(
            "ALTER TABLE diagnostic_task_operations ADD COLUMN IF NOT EXISTS request_id VARCHAR(128)"
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_operations_request_id ON diagnostic_task_operations (request_id)"
        )
        await connection.execute(CREATE_SECURITY_AUDIT_SQL)
        await connection.execute(CREATE_SECURITY_AUDIT_INDEX_SQL)
        await connection.execute(
            "ALTER TABLE security_audit ADD COLUMN IF NOT EXISTS request_id VARCHAR(128)"
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_request_id ON diagnostic_tasks (request_id)"
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_request_id ON security_audit (request_id)"
        )
        await recover_stale_diagnosis_tasks(
            pool,
            max(1, math.ceil(settings.diagnosis_task_time_limit_seconds / 60) + 5),
        )
        await connection.execute(CREATE_DOCUMENT_CHUNKS_SQL)
        await connection.execute(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding JSONB"
        )
        await connection.execute(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64)"
        )
        await connection.execute(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_dimension SMALLINT"
        )
        await connection.execute(CREATE_DOCUMENT_CHUNKS_INDEX_SQL)
        await connection.execute(CREATE_DOCUMENT_INDEX_RUNS_SQL)
        await connection.execute(
            "ALTER TABLE document_index_runs ADD COLUMN IF NOT EXISTS repository_revision VARCHAR(128)"
        )
        await connection.execute(
            "ALTER TABLE document_index_runs ADD COLUMN IF NOT EXISTS index_version VARCHAR(32) NOT NULL DEFAULT 'v1'"
        )
        await connection.execute(CREATE_DOCUMENT_INDEX_RUNS_INDEX_SQL)
    return pool


def _decode_task(row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
    """将 asyncpg 记录转换为可 JSON 序列化的任务字典。

    参数:
        - `row`：单条数据库记录，类型为 `Optional[asyncpg.Record]`。
    """
    if row is None:
        return None
    task = dict(row)
    if isinstance(task.get("result"), str):
        task["result"] = json.loads(task["result"])
    if isinstance(task.get("validation_plan"), str):
        task["validation_plan"] = json.loads(task["validation_plan"])
    for key in ("created_at", "updated_at"):
        value = task.get(key)
        if isinstance(value, datetime):
            task[key] = value.isoformat()
    return task


async def insert_task(
    pool: asyncpg.Pool,
    task_id: str,
    project: str,
    question: str,
    environment: str,
    created_by: Optional[str] = None,
    validation_plan: Optional[Dict[str, Any]] = None,
    use_ai: Optional[bool] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """插入排队状态的诊断任务，并保存可选的只读验证计划。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `question`：用户提交的诊断问题，类型为 `str`。
        - `environment`：诊断目标环境，类型为 `str`。
        - `created_by`：任务创建者标识，类型为 `Optional[str]`。
        - `validation_plan`：创建任务时提交的验证计划，类型为 `Optional[Dict[str, Any]]`。
        - `request_id`：HTTP 请求关联 ID，类型为 `Optional[str]`。
    """
    row = await pool.fetchrow(
        """
        INSERT INTO diagnostic_tasks
            (id, project, question, environment, status, progress, current_step, created_by, validation_plan, use_ai, request_id)
        VALUES ($1, $2, $3, $4, 'QUEUED', 0, '等待 Worker 执行', $5, $6::jsonb, $7, $8)
        RETURNING *
        """,
        task_id,
        project,
        question,
        environment,
        created_by,
        json.dumps(validation_plan, ensure_ascii=False) if validation_plan is not None else None,
        use_ai,
        request_id,
    )
    return _decode_task(row)  # type: ignore[return-value]


async def get_task(pool: asyncpg.Pool, task_id: str) -> Optional[Dict[str, Any]]:
    """根据任务 ID 查询任务，不存在时返回 None。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    row = await pool.fetchrow("SELECT * FROM diagnostic_tasks WHERE id = $1", task_id)
    return _decode_task(row)


async def list_tasks(
    pool: asyncpg.Pool,
    *,
    project: Optional[str] = None,
    status: Optional[str] = None,
    query: Optional[str] = None,
    request_id: Optional[str] = None,
    days: Optional[int] = None,
    offset: int = 0,
    limit: int = 20,
) -> list[Dict[str, Any]]:
    """按筛选条件分页读取历史诊断任务，并仅返回报告列表所需字段。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `Optional[str]`。
        - `status`：状态值，类型为 `Optional[str]`。
        - `query`：检索或查询文本，类型为 `Optional[str]`。
        - `request_id`：HTTP 请求关联 ID，类型为 `Optional[str]`。
        - `days`：最近天数范围，类型为 `Optional[int]`。
        - `offset`：分页起始偏移，类型为 `int`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    conditions = []
    values: list[Any] = []

    def add_condition(sql: str, value: Any) -> None:
        """追加带位置参数的安全筛选条件。

        参数:
            - `sql`：待执行或展示的 SQL，类型为 `str`。
            - `value`：SQL 条件占位符对应的绑定参数值，类型为 `Any`。
        """
        values.append(value)
        conditions.append(sql.replace("$VALUE", f"${len(values)}"))

    if project:
        add_condition("project = $VALUE", project)
    if status:
        add_condition("status = $VALUE", status)
    if query:
        add_condition("question ILIKE '%' || $VALUE || '%'", query)
    if request_id:
        add_condition("request_id = $VALUE", request_id)
    if days is not None:
        add_condition("created_at >= NOW() - ($VALUE * INTERVAL '1 day')", days)
    offset_position = len(values) + 1
    limit_position = len(values) + 2
    values.extend([offset, limit])
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"""
        SELECT id, project, question, environment, status, progress, current_step, use_ai, request_id,
               error, created_at, updated_at
        FROM diagnostic_tasks
        {where}
        ORDER BY created_at DESC, id DESC
        OFFSET ${offset_position} LIMIT ${limit_position}
        """,
        *values,
    )
    tasks = []
    for row in rows:
        task = dict(row)
        for key in ("created_at", "updated_at"):
            value = task.get(key)
            if isinstance(value, datetime):
                task[key] = value.isoformat()
        tasks.append(task)
    return tasks


async def update_task(
    pool: asyncpg.Pool,
    task_id: str,
    *,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    current_step: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """按需更新任务状态、进度、结果或错误信息。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `status`：状态值，类型为 `Optional[str]`。
        - `progress`：任务完成百分比，类型为 `Optional[int]`。
        - `current_step`：任务当前执行步骤说明，类型为 `Optional[str]`。
        - `result`：任务完成时需要持久化的诊断报告结果，类型为 `Optional[Dict[str, Any]]`。
        - `error`：失败原因或数据库异常，类型为 `Optional[str]`。
    """
    row = await pool.fetchrow(
        """
        UPDATE diagnostic_tasks
        SET status = COALESCE($2, status),
            progress = COALESCE($3, progress),
            current_step = COALESCE($4, current_step),
            result = COALESCE($5::jsonb, result),
            error = COALESCE($6, error),
            updated_at = NOW()
        WHERE id = $1
        RETURNING *
        """,
        task_id,
        status,
        progress,
        current_step,
        json.dumps(result, ensure_ascii=False) if result is not None else None,
        error,
    )
    return _decode_task(row)


async def mark_cancelled(pool: asyncpg.Pool, task_id: str) -> Optional[Dict[str, Any]]:
    """将未结束任务原子地标记为已取消。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    row = await pool.fetchrow(
        """
        UPDATE diagnostic_tasks
        SET status = 'CANCELLED', progress = 100, current_step = '任务已取消', updated_at = NOW()
        WHERE id = $1 AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
        RETURNING *
        """,
        task_id,
    )
    return _decode_task(row)


async def mark_cancelled_many(pool: asyncpg.Pool, project: str, task_ids: list[str]) -> list[Dict[str, Any]]:
    """批量取消项目内尚未结束的诊断任务。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `project`：任务必须属于的诊断项目。
        - `task_ids`：最多 20 个需要尝试取消的任务 ID。
    """
    rows = await pool.fetch(
        """
        UPDATE diagnostic_tasks
        SET status = 'CANCELLED',
            progress = 100,
            current_step = '任务已批量取消',
            updated_at = NOW()
        WHERE project = $1
          AND id = ANY($2::varchar[])
          AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
        RETURNING id, status
        """,
        project,
        task_ids,
    )
    return [dict(row) for row in rows]


async def prepare_task_retry(
    pool: asyncpg.Pool, task_id: str, max_retries: int = 3
) -> Optional[Dict[str, Any]]:
    """将失败任务原子重置为排队状态，并限制人工重试次数。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `max_retries`：任务允许的最大重试次数，类型为 `int`。
    """
    row = await pool.fetchrow(
        """
        UPDATE diagnostic_tasks
        SET status = 'QUEUED',
            progress = 0,
            current_step = '等待 Worker 重新执行',
            result = NULL,
            error = NULL,
            retry_count = retry_count + 1,
            updated_at = NOW()
        WHERE id = $1 AND status = 'FAILED' AND retry_count < $2
        RETURNING *
        """,
        task_id,
        max_retries,
    )
    return _decode_task(row)


async def clear_task_evidence(pool: asyncpg.Pool, task_id: str) -> None:
    """清理任务旧证据，避免人工重试后重复保存相同上下文。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    await pool.execute("DELETE FROM diagnostic_evidence WHERE task_id = $1", task_id)


async def insert_task_operation(
    pool: asyncpg.Pool,
    task_id: str,
    operation: str,
    *,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    actor: Optional[str] = None,
    request_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """写入任务操作审计，调用方应传入不含敏感信息的操作摘要。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `operation`：操作类型，类型为 `str`。
        - `from_status`：状态变更前值，类型为 `Optional[str]`。
        - `to_status`：状态变更后值，类型为 `Optional[str]`。
        - `actor`：操作主体标识，类型为 `Optional[str]`。
        - `request_id`：HTTP 请求关联 ID，类型为 `Optional[str]`。
        - `detail`：结构化补充信息，类型为 `Optional[Dict[str, Any]]`。
    """
    await pool.execute(
        """
        INSERT INTO diagnostic_task_operations
            (task_id, operation, from_status, to_status, actor, request_id, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        task_id,
        operation,
        from_status,
        to_status,
        actor,
        request_id[:128] if request_id else None,
        json.dumps(detail or {}, ensure_ascii=False),
    )


async def insert_security_audit(
    pool: asyncpg.Pool,
    event_type: str,
    status_code: int,
    reason: str,
    method: str,
    path: str,
    *,
    client_address: Optional[str] = None,
    actor: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """写入认证或授权拒绝摘要，调用方不得传入请求头或原始 API Key。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `event_type`：安全审计事件类型，类型为 `str`。
        - `status_code`：HTTP 响应状态码，类型为 `int`。
        - `reason`：安全拒绝原因，类型为 `str`。
        - `method`：HTTP 请求方法，类型为 `str`。
        - `path`：文件或请求路径，类型为 `str`。
        - `client_address`：调用方客户端地址，类型为 `Optional[str]`。
        - `actor`：操作主体标识，类型为 `Optional[str]`。
        - `request_id`：HTTP 请求关联 ID，类型为 `Optional[str]`。
    """
    await pool.execute(
        """
        INSERT INTO security_audit
            (event_type, status_code, reason, method, path, client_address, actor, request_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        event_type[:32],
        status_code,
        reason[:500],
        method[:16],
        path[:500],
        client_address[:100] if client_address else None,
        actor[:100] if actor else None,
        request_id[:128] if request_id else None,
    )


async def list_task_operations(
    pool: asyncpg.Pool, task_id: str, limit: int = 100
) -> list[Dict[str, Any]]:
    """按时间倒序返回任务操作审计轨迹，并限制单次读取数量。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    rows = await pool.fetch(
        """
        SELECT id, task_id, operation, from_status, to_status, actor, request_id, detail, created_at
        FROM diagnostic_task_operations
        WHERE task_id = $1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        task_id,
        limit,
    )
    operations = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = item["created_at"].isoformat()
        if isinstance(item.get("detail"), str):
            item["detail"] = json.loads(item["detail"])
        operations.append(item)
    return operations


def _decode_trace_row(row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
    """把诊断轨迹记录转换为可 JSON 序列化的字典。

    参数:
        - `row`：`diagnosis_trace_spans` 表记录或测试替身返回值。

    Returns:
        负载已解析且时间为 ISO 8601 的轨迹字典；空记录返回 `None`。
    """
    if row is None:
        return None
    item = dict(row)
    if isinstance(item.get("payload"), str):
        try:
            item["payload"] = json.loads(item["payload"])
        except (TypeError, ValueError):
            item["payload"] = {}
    if isinstance(item.get("created_at"), datetime):
        item["created_at"] = item["created_at"].isoformat()
    if item.get("elapsed_ms") is not None:
        item["elapsed_ms"] = round(float(item["elapsed_ms"]), 3)
    return item


async def record_trace_span(
    pool: asyncpg.Pool,
    task_id: str,
    event_type: str,
    stage: str,
    status: str,
    *,
    elapsed_ms: Optional[float] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """写入一条脱敏诊断阶段轨迹，轨迹故障不阻断主诊断流程。

    参数:
        - `pool`：RAgent 元数据库连接池或测试替身。
        - `task_id`：已经存在的诊断任务 ID；外键保证轨迹可追溯到任务。
        - `event_type`：稳定的阶段事件类型，例如 `stage.completed`。
        - `stage`：面向用户的阶段标识，例如 `retrieval` 或 `ai_narration`。
        - `status`：阶段结果状态，例如 `started`、`succeeded`、`failed` 或 `degraded`。
        - `elapsed_ms`：该阶段耗时毫秒数；未知时为空。
        - `payload`：仅含计数、模型名和降级原因等脱敏摘要。

    Returns:
        已写入轨迹的字典；数据库或测试替身异常时返回 `None`，调用方继续诊断。
    """
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO diagnosis_trace_spans
                (task_id, event_type, stage, status, elapsed_ms, payload)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id, task_id, event_type, stage, status, elapsed_ms, payload, created_at
            """,
            task_id,
            event_type[:64],
            stage[:64],
            status[:24],
            elapsed_ms,
            json.dumps(payload or {}, ensure_ascii=False),
        )
    except Exception:
        return None
    return _decode_trace_row(row)


async def list_trace_spans(
    pool: asyncpg.Pool,
    task_id: str,
    *,
    after_id: int = 0,
    limit: int = 200,
) -> list[Dict[str, Any]]:
    """按写入顺序返回任务诊断阶段的脱敏轨迹。

    参数:
        - `pool`：RAgent 元数据库连接池或测试替身。
        - `task_id`：需要读取轨迹的诊断任务 ID。
        - `after_id`：SSE 已消费的最后轨迹 ID；首次读取传 0。
        - `limit`：单次最多返回的轨迹数量，API 层限制在 1 到 500。

    Returns:
        按自增 ID 升序排列的轨迹列表；不存在轨迹时返回空列表。
    """
    rows = await pool.fetch(
        """
        SELECT id, task_id, event_type, stage, status, elapsed_ms, payload, created_at
        FROM diagnosis_trace_spans
        WHERE task_id = $1 AND id > $2
        ORDER BY id ASC
        LIMIT $3
        """,
        task_id,
        after_id,
        limit,
    )
    return [item for row in rows if (item := _decode_trace_row(row)) is not None]


async def insert_evidence(pool: asyncpg.Pool, task_id: str, evidence: Dict[str, Any]) -> None:
    """保存一条诊断证据，并记录其来源工具和上下文。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `evidence`：诊断证据记录，类型为 `Dict[str, Any]`。
    """
    await pool.execute(
        """
        INSERT INTO diagnostic_evidence
            (task_id, evidence_type, project, file_path, line_start, line_end, query, content, source)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        """,
        task_id,
        evidence.get("type", "code"),
        evidence.get("project"),
        evidence.get("file"),
        evidence.get("line"),
        evidence.get("end_line", evidence.get("line")),
        evidence.get("query"),
        evidence.get("content"),
        json.dumps(evidence.get("source", {"tool": "search_code"}), ensure_ascii=False),
    )


async def _replace_symbol_index_in_transaction(
    connection: Any,
    project: str,
    index: Dict[str, Any],
) -> int:
    """在调用方事务内批量替换项目符号，不自行提交或回滚。

    参数:
        - `connection`：已经进入事务的 asyncpg 连接或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `index`：项目代码或符号索引，类型为 `Dict[str, Any]`。

    Returns:
        写入的符号数量。大型项目的索引通常包含数万符号和引用，逐条 `execute`
        会产生数万次数据库往返；这里使用每批 1000 条 `executemany`，同时保持
        单事务原子替换，失败时不会留下半份索引。
    """
    symbols = index.get("symbols", [])
    references = index.get("references", [])
    references_by_source: Dict[str, list] = {}
    for reference in references:
        source = reference.get("source")
        if source:
            references_by_source.setdefault(source, []).append(reference)
    await connection.execute("DELETE FROM code_symbols WHERE project = $1", project)
    batch = []
    for symbol in symbols:
        batch.append(
            (
                project,
                symbol["symbol_type"],
                symbol["symbol_name"],
                symbol["file"],
                symbol["line"],
                symbol.get("signature"),
                json.dumps(
                    references_by_source.get(symbol["symbol_name"], []),
                    ensure_ascii=False,
                ),
            )
        )
        if len(batch) >= 1000:
            await connection.executemany(
                """
                INSERT INTO code_symbols
                    (project, symbol_type, symbol_name, file_path, line, signature, "references")
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                batch,
            )
            batch.clear()
    if batch:
        await connection.executemany(
            """
            INSERT INTO code_symbols
                (project, symbol_type, symbol_name, file_path, line, signature, "references")
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            batch,
        )
    return len(symbols)


async def replace_symbol_index(
    pool: asyncpg.Pool, project: str, index: Dict[str, Any]
) -> int:
    """原子替换项目符号索引，并返回写入的符号数量。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `index`：项目代码或符号索引，类型为 `Dict[str, Any]`。

    该入口保留给显式重建索引的调用方；诊断链路使用
    `replace_symbol_index_if_changed`，同一代码快照不会重复删除并写入。
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            return await _replace_symbol_index_in_transaction(connection, project, index)


async def replace_symbol_index_if_changed(
    pool: asyncpg.Pool,
    project: str,
    repository_fingerprint: str,
    index: Dict[str, Any],
) -> Dict[str, Any]:
    """仅在代码快照或索引计数变化时重写项目符号表。

    参数:
        - `pool`：RAgent 元数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：当前诊断项目，类型为 `str`；数据库咨询锁按项目隔离。
        - `repository_fingerprint`：当前 PHP 文件快照 SHA-256，类型为 `str`。
        - `index`：当前任务从缓存或仓库得到的完整符号索引。

    Returns:
        `updated`、`symbol_count` 和 `previous_symbol_count` 审计信息。代码指纹与
        四类索引计数都相同时跳过删除和插入，多个并发任务不会争抢同一项目的
        四万级写事务；任何计数变化都会完整替换，避免部分更新产生混合版本。
    """
    counts = {
        "symbol_count": len(index.get("symbols") or []),
        "reference_count": len(index.get("references") or []),
        "config_count": len(index.get("configs") or []),
        "sql_count": len(index.get("sql") or []),
    }
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"project-symbol-index:{project}",
            )
            existing = await connection.fetchrow(
                """
                SELECT repository_fingerprint, symbol_count, reference_count,
                       config_count, sql_count
                FROM project_symbol_indexes
                WHERE project = $1
                """,
                project,
            )
            if existing is not None and all(
                (
                    str(existing["repository_fingerprint"]) == repository_fingerprint,
                    int(existing["symbol_count"]) == counts["symbol_count"],
                    int(existing["reference_count"]) == counts["reference_count"],
                    int(existing["config_count"]) == counts["config_count"],
                    int(existing["sql_count"]) == counts["sql_count"],
                )
            ):
                return {
                    "updated": False,
                    "symbol_count": counts["symbol_count"],
                    "previous_symbol_count": int(existing["symbol_count"]),
                }
            previous_symbol_count = int(existing["symbol_count"]) if existing else 0
            await _replace_symbol_index_in_transaction(connection, project, index)
            await connection.execute(
                """
                INSERT INTO project_symbol_indexes
                    (project, repository_fingerprint, symbol_count, reference_count,
                     config_count, sql_count, indexed_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (project) DO UPDATE SET
                    repository_fingerprint = EXCLUDED.repository_fingerprint,
                    symbol_count = EXCLUDED.symbol_count,
                    reference_count = EXCLUDED.reference_count,
                    config_count = EXCLUDED.config_count,
                    sql_count = EXCLUDED.sql_count,
                    indexed_at = EXCLUDED.indexed_at
                """,
                project,
                repository_fingerprint,
                counts["symbol_count"],
                counts["reference_count"],
                counts["config_count"],
                counts["sql_count"],
            )
            return {
                "updated": True,
                "symbol_count": counts["symbol_count"],
                "previous_symbol_count": previous_symbol_count,
            }


async def insert_validation_audit(
    pool: asyncpg.Pool,
    operation: str,
    status: str,
    *,
    task_id: Optional[str] = None,
    project: Optional[str] = None,
    request_summary: Optional[Dict[str, Any]] = None,
    result_summary: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """写入一条验证工具审计记录，调用方应传入已脱敏摘要。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `operation`：操作类型，类型为 `str`。
        - `status`：状态值，类型为 `str`。
        - `task_id`：诊断任务 ID，类型为 `Optional[str]`。
        - `project`：项目标识，类型为 `Optional[str]`。
        - `request_summary`：验证请求脱敏摘要，类型为 `Optional[Dict[str, Any]]`。
        - `result_summary`：验证结果脱敏摘要，类型为 `Optional[Dict[str, Any]]`。
        - `error`：失败原因或数据库异常，类型为 `Optional[str]`。
    """
    await pool.execute(
        """
        INSERT INTO validation_audit
            (task_id, operation, project, status, request_summary, result_summary, error)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
        """,
        task_id,
        operation,
        project,
        status,
        json.dumps(request_summary or {}, ensure_ascii=False),
        json.dumps(result_summary or {}, ensure_ascii=False),
        error,
    )


def _decode_feedback(row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
    """将反馈记录转换为接口可返回的字典，并规范化创建时间。

    参数:
        - `row`：单条数据库记录，类型为 `Optional[asyncpg.Record]`。
    """
    if row is None:
        return None
    feedback = dict(row)
    created_at = feedback.get("created_at")
    if isinstance(created_at, datetime):
        feedback["created_at"] = created_at.isoformat()
    return feedback


async def insert_feedback(
    pool: asyncpg.Pool,
    task_id: str,
    rating: str,
    comment: Optional[str] = None,
    submitted_by: Optional[str] = None,
) -> Dict[str, Any]:
    """保存一条诊断任务人工反馈，并返回脱离数据库类型的记录。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `rating`：人工反馈类型，类型为 `str`。
        - `comment`：人工反馈说明，类型为 `Optional[str]`。
        - `submitted_by`：反馈提交者标识，类型为 `Optional[str]`。
    """
    row = await pool.fetchrow(
        """
        INSERT INTO diagnostic_feedback (task_id, rating, comment, submitted_by)
        VALUES ($1, $2, $3, $4)
        RETURNING id, task_id, rating, comment, submitted_by, created_at
        """,
        task_id,
        rating,
        comment,
        submitted_by,
    )
    return _decode_feedback(row)  # type: ignore[return-value]


async def list_feedback(
    pool: asyncpg.Pool, task_id: str, limit: int = 50
) -> list[Dict[str, Any]]:
    """按时间倒序返回诊断任务反馈，并限制单次读取数量。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    rows = await pool.fetch(
        """
        SELECT id, task_id, rating, comment, submitted_by, created_at
        FROM diagnostic_feedback
        WHERE task_id = $1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        task_id,
        limit,
    )
    feedback_items = []
    for row in rows:
        feedback = _decode_feedback(row)
        if feedback is not None:
            feedback_items.append(feedback)
    return feedback_items


async def summarize_feedback(pool: asyncpg.Pool, task_id: str) -> Dict[str, Any]:
    """汇总任务反馈数量和最近反馈时间，供质量看板使用。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    rows = await pool.fetch(
        """
        SELECT rating, COUNT(*) AS count, MAX(created_at) AS latest_at
        FROM diagnostic_feedback
        WHERE task_id = $1
        GROUP BY rating
        """,
        task_id,
    )
    counts = {
        "correct": 0,
        "inaccurate": 0,
        "insufficient_evidence": 0,
        "escalate": 0,
    }
    latest_at = None
    for row in rows:
        rating = row["rating"]
        if rating in counts:
            counts[rating] = int(row["count"])
        candidate = row["latest_at"]
        if candidate is not None and (latest_at is None or candidate > latest_at):
            latest_at = candidate
    if latest_at is not None:
        latest_at = latest_at.isoformat() if isinstance(latest_at, datetime) else str(latest_at)
    return {"total": sum(counts.values()), "counts": counts, "latest_at": latest_at}


async def summarize_project_feedback(pool: asyncpg.Pool, project: str) -> Dict[str, Any]:
    """按项目汇总多个诊断任务的反馈数量、任务数和最近反馈时间。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
    """
    rows = await pool.fetch(
        """
        SELECT f.rating, COUNT(*) AS count, COUNT(DISTINCT f.task_id) AS task_count,
               (
                   SELECT COUNT(DISTINCT f2.task_id)
                   FROM diagnostic_feedback AS f2
                   JOIN diagnostic_tasks AS t2 ON t2.id = f2.task_id
                   WHERE t2.project = $1
               ) AS project_task_count,
               MAX(f.created_at) AS latest_at
        FROM diagnostic_feedback AS f
        JOIN diagnostic_tasks AS t ON t.id = f.task_id
        WHERE t.project = $1
        GROUP BY f.rating
        """,
        project,
    )
    counts = {
        "correct": 0,
        "inaccurate": 0,
        "insufficient_evidence": 0,
        "escalate": 0,
    }
    task_count = 0
    latest_at = None
    for row in rows:
        rating = row["rating"]
        if rating in counts:
            counts[rating] = int(row["count"])
        task_count = max(
            task_count,
            int(row.get("project_task_count", row.get("task_count", 0))),
        )
        candidate = row["latest_at"]
        if candidate is not None and (latest_at is None or candidate > latest_at):
            latest_at = candidate
    if latest_at is not None:
        latest_at = latest_at.isoformat() if isinstance(latest_at, datetime) else str(latest_at)
    return {
        "total": sum(counts.values()),
        "task_count": task_count,
        "counts": counts,
        "latest_at": latest_at,
    }


async def start_document_index_run(pool: asyncpg.Pool, project: str) -> int:
    """创建一条运行中的文档索引记录并返回运行 ID。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
    """
    return await start_document_index_run_with_revision(pool, project)


async def find_reusable_document_index_run(
    pool: asyncpg.Pool,
    project: str,
    repository_revision: str,
    index_version: str = "v1",
) -> Optional[Dict[str, Any]]:
    """返回同仓库版本、同索引版本且分块完整的最近成功运行。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `repository_revision`：仓库提交版本，类型为 `str`。
        - `index_version`：文档索引版本，类型为 `str`。
    """
    row = await pool.fetchrow(
        """
        SELECT r.id, r.chunk_count, r.repository_revision, r.index_version,
               r.finished_at, COUNT(c.id) AS actual_chunks
        FROM document_index_runs AS r
        JOIN document_chunks AS c ON c.project = r.project
        WHERE r.project = $1
          AND r.status = 'COMPLETED'
          AND r.repository_revision = $2
          AND r.index_version = $3
        GROUP BY r.id
        HAVING r.chunk_count > 0 AND COUNT(c.id) = r.chunk_count
        ORDER BY r.finished_at DESC NULLS LAST, r.id DESC
        LIMIT 1
        """,
        project,
        repository_revision,
        index_version,
    )
    return dict(row) if row is not None else None


async def recover_stale_document_index_runs(
    pool: asyncpg.Pool, project: str, stale_minutes: int = 60
) -> int:
    """将项目超时未结束的索引运行标记为失败，并返回恢复数量。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `stale_minutes`：判定悬挂状态的分钟数，类型为 `int`。
    """
    if stale_minutes < 1 or stale_minutes > 7 * 24 * 60:
        raise ValueError("索引超时窗口必须在 1 分钟到 7 天之间")
    result = await pool.execute(
        """
        UPDATE document_index_runs
        SET status = 'FAILED',
            error = '索引运行超时，已由新运行恢复',
            finished_at = NOW()
        WHERE project = $1
          AND status = 'RUNNING'
          AND started_at < NOW() - ($2 * INTERVAL '1 minute')
        """,
        project,
        stale_minutes,
    )
    return int(result.rsplit(" ", 1)[-1]) if result.rsplit(" ", 1)[-1].isdigit() else 0


async def recover_stale_diagnosis_tasks(
    pool: asyncpg.Pool, stale_minutes: int = 10
) -> int:
    """将超过执行窗口仍处于运行阶段的任务标记为失败，恢复 Worker 中断状态。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `stale_minutes`：判定悬挂状态的分钟数，类型为 `int`。
    """
    if stale_minutes < 1 or stale_minutes > 7 * 24 * 60:
        raise ValueError("诊断任务超时窗口必须在 1 分钟到 7 天之间")
    result = await pool.execute(
        """
        UPDATE diagnostic_tasks
        SET status = 'FAILED',
            progress = 100,
            current_step = '任务执行超时，已由系统恢复',
            error = '任务超过执行超时窗口或 Worker 已中断',
            updated_at = NOW()
        WHERE status IN ('RUNNING', 'SEARCHING', 'VERIFYING', 'REPORTING')
          AND updated_at < NOW() - ($1 * INTERVAL '1 minute')
        """,
        stale_minutes,
    )
    return int(result.rsplit(" ", 1)[-1]) if result.rsplit(" ", 1)[-1].isdigit() else 0


async def start_document_index_run_with_revision(
    pool: asyncpg.Pool,
    project: str,
    repository_revision: Optional[str] = None,
    index_version: str = "v1",
) -> int:
    """创建带仓库提交版本和索引版本的运行记录。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `repository_revision`：仓库提交版本，类型为 `Optional[str]`。
        - `index_version`：文档索引版本，类型为 `str`。
    """
    row = await pool.fetchrow(
        """
        INSERT INTO document_index_runs (project, status, repository_revision, index_version)
        VALUES ($1, 'RUNNING', $2, $3)
        RETURNING id
        """,
        project,
        repository_revision,
        index_version,
    )
    return int(row["id"])


async def finish_document_index_run(
    pool: asyncpg.Pool,
    run_id: int,
    status: str,
    chunk_count: int = 0,
    error: Optional[str] = None,
) -> None:
    """完成文档索引运行记录，保存数量或受控错误信息。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `run_id`：运行记录 ID，类型为 `int`。
        - `status`：状态值，类型为 `str`。
        - `chunk_count`：文档分块数量，类型为 `int`。
        - `error`：失败原因或数据库异常，类型为 `Optional[str]`。
    """
    await pool.execute(
        """
        UPDATE document_index_runs
        SET status = $2, chunk_count = $3, error = $4, finished_at = NOW()
        WHERE id = $1
        """,
        run_id,
        status,
        chunk_count,
        error,
    )


async def cleanup_document_index_runs(pool: asyncpg.Pool, project: str, keep: int = 50) -> int:
    """保留项目最近若干条索引运行记录并删除更早的终态记录。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `keep`：每个项目保留的索引运行记录数，类型为 `int`。
    """
    if keep < 1 or keep > 1000:
        raise ValueError("索引运行保留数量必须在 1 到 1000 之间")
    result = await pool.execute(
        """
        DELETE FROM document_index_runs
        WHERE project = $1
          AND status IN ('COMPLETED', 'FAILED')
          AND id NOT IN (
              SELECT id FROM document_index_runs
              WHERE project = $1
              ORDER BY started_at DESC
              LIMIT $2
          )
        """,
        project,
        keep,
    )
    return int(result.rsplit(" ", 1)[-1]) if result.rsplit(" ", 1)[-1].isdigit() else 0
