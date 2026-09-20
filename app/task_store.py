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


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    async with pool.acquire() as connection:
        await connection.execute(CREATE_TASKS_SQL)
    return pool


def _decode_task(row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
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
