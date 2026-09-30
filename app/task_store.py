"""诊断任务和证据的 PostgreSQL 持久化操作。"""

import json
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


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """创建数据库连接池，并确保任务和证据表存在。"""
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    async with pool.acquire() as connection:
        await connection.execute(CREATE_TASKS_SQL)
        await connection.execute(
            "ALTER TABLE diagnostic_tasks ADD COLUMN IF NOT EXISTS validation_plan JSONB"
        )
        await connection.execute(CREATE_EVIDENCE_SQL)
        await connection.execute(CREATE_SYMBOLS_SQL)
        await connection.execute(CREATE_VALIDATION_AUDIT_SQL)
        await connection.execute(CREATE_VALIDATION_AUDIT_INDEX_SQL)
        await connection.execute(CREATE_FEEDBACK_SQL)
        await connection.execute(CREATE_FEEDBACK_INDEX_SQL)
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
    """将 asyncpg 记录转换为可 JSON 序列化的任务字典。"""
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
) -> Dict[str, Any]:
    """插入排队状态的诊断任务，并保存可选的只读验证计划。"""
    row = await pool.fetchrow(
        """
        INSERT INTO diagnostic_tasks
            (id, project, question, environment, status, progress, current_step, created_by, validation_plan)
        VALUES ($1, $2, $3, $4, 'QUEUED', 0, '等待 Worker 执行', $5, $6::jsonb)
        RETURNING *
        """,
        task_id,
        project,
        question,
        environment,
        created_by,
        json.dumps(validation_plan, ensure_ascii=False) if validation_plan is not None else None,
    )
    return _decode_task(row)  # type: ignore[return-value]


async def get_task(pool: asyncpg.Pool, task_id: str) -> Optional[Dict[str, Any]]:
    """根据任务 ID 查询任务，不存在时返回 None。"""
    row = await pool.fetchrow("SELECT * FROM diagnostic_tasks WHERE id = $1", task_id)
    return _decode_task(row)


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
    """按需更新任务状态、进度、结果或错误信息。"""
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
    """将未结束任务原子地标记为已取消。"""
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


async def insert_evidence(pool: asyncpg.Pool, task_id: str, evidence: Dict[str, Any]) -> None:
    """保存一条诊断证据，并记录其来源工具和上下文。"""
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


async def replace_symbol_index(
    pool: asyncpg.Pool, project: str, index: Dict[str, Any]
) -> int:
    """原子替换项目符号索引，并返回写入的符号数量。"""
    symbols = index.get("symbols", [])
    references = index.get("references", [])
    references_by_source: Dict[str, list] = {}
    for reference in references:
        source = reference.get("source")
        if source:
            references_by_source.setdefault(source, []).append(reference)
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("DELETE FROM code_symbols WHERE project = $1", project)
            for symbol in symbols:
                await connection.execute(
                    """
                    INSERT INTO code_symbols
                        (project, symbol_type, symbol_name, file_path, line, signature, "references")
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                    """,
                    project,
                    symbol["symbol_type"],
                    symbol["symbol_name"],
                    symbol["file"],
                    symbol["line"],
                    symbol.get("signature"),
                    json.dumps(references_by_source.get(symbol["symbol_name"], []), ensure_ascii=False),
                )
    return len(symbols)


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
    """写入一条验证工具审计记录，调用方应传入已脱敏摘要。"""
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
    """将反馈记录转换为接口可返回的字典，并规范化创建时间。"""
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
    """保存一条诊断任务人工反馈，并返回脱离数据库类型的记录。"""
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
    """按时间倒序返回诊断任务反馈，并限制单次读取数量。"""
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


async def start_document_index_run(pool: asyncpg.Pool, project: str) -> int:
    """创建一条运行中的文档索引记录并返回运行 ID。"""
    return await start_document_index_run_with_revision(pool, project)


async def recover_stale_document_index_runs(
    pool: asyncpg.Pool, project: str, stale_minutes: int = 60
) -> int:
    """将项目超时未结束的索引运行标记为失败，并返回恢复数量。"""
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


async def start_document_index_run_with_revision(
    pool: asyncpg.Pool,
    project: str,
    repository_revision: Optional[str] = None,
    index_version: str = "v1",
) -> int:
    """创建带仓库提交版本和索引版本的运行记录。"""
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
    """完成文档索引运行记录，保存数量或受控错误信息。"""
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
    """保留项目最近若干条索引运行记录并删除更早的终态记录。"""
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
