"""AI 评测运行结果持久化和查询。"""

import json
from typing import Any, Dict, List, Optional


async def insert_ai_evaluation_run(
    pool,
    project: str,
    result: Dict[str, Any],
    *,
    status: str = "COMPLETED",
    error: Optional[str] = None,
) -> int:
    """保存一次 AI 评测汇总并返回运行记录 ID。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `project`：本次评测覆盖的项目标识。
        - `result`：AI 评测汇总，包含通过率、证据召回和用例明细。
        - `status`：评测运行终态，默认 COMPLETED。
        - `error`：评测整体失败时的受控错误摘要。
    """
    row = await pool.fetchrow(
        """
        INSERT INTO ai_evaluation_runs
            (project, status, min_pass_rate, total, executed, skipped, passed,
             pass_rate, average_evidence_ref_recall, result, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
        RETURNING id
        """,
        project,
        status,
        float(result.get("min_pass_rate", 0.0)),
        int(result.get("total", 0)),
        int(result.get("executed", 0)),
        int(result.get("skipped", 0)),
        int(result.get("passed", 0)),
        float(result.get("pass_rate", 0.0)),
        float(result.get("average_evidence_ref_recall", 0.0)),
        json.dumps(result, ensure_ascii=False),
        error,
    )
    return int(row["id"])


async def list_ai_evaluation_runs(pool, project: str, limit: int = 20) -> List[Dict[str, Any]]:
    """按时间倒序返回项目 AI 评测运行记录。

    参数:
        - `pool`：RAgent 元数据库连接池。
        - `project`：需要查询评测历史的项目标识。
        - `limit`：单次最多返回的评测运行数量。
    """
    rows = await pool.fetch(
        """
        SELECT id, project, status, min_pass_rate, total, executed, skipped,
               passed, pass_rate, average_evidence_ref_recall, error, created_at
        FROM ai_evaluation_runs
        WHERE project = $1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        project,
        limit,
    )
    runs = []
    for row in rows:
        item = dict(row)
        created_at = item.get("created_at")
        if hasattr(created_at, "isoformat"):
            item["created_at"] = created_at.isoformat()
        runs.append(item)
    return runs
