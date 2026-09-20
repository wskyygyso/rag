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


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """创建数据库连接池，并确保任务和证据表存在。"""
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    async with pool.acquire() as connection:
        await connection.execute(CREATE_TASKS_SQL)
        await connection.execute(CREATE_EVIDENCE_SQL)
        await connection.execute(CREATE_SYMBOLS_SQL)
    return pool


def _decode_task(row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
    """将 asyncpg 记录转换为可 JSON 序列化的任务字典。"""
    if row is None:
        return None
    task = dict(row)
    if isinstance(task.get("result"), str):
        task["result"] = json.loads(task["result"])
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
) -> Dict[str, Any]:
    """插入一条排队状态的诊断任务记录。"""
    row = await pool.fetchrow(
        """
        INSERT INTO diagnostic_tasks
            (id, project, question, environment, status, progress, current_step, created_by)
        VALUES ($1, $2, $3, $4, 'QUEUED', 0, '等待 Worker 执行', $5)
        RETURNING *
        """,
        task_id,
        project,
        question,
        environment,
        created_by,
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
