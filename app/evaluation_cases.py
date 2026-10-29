"""从历史正确反馈生成待人工审核的诊断评测候选。"""

import json
from typing import Any, Dict, Iterable, List, Optional


def build_candidate(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """将已完成且有证据的任务转换为待审核候选，缺少证据时返回 None。"""
    result = task.get("result") or {}
    if isinstance(result, str):
        result = json.loads(result)
    evidence = result.get("evidence") or []
    symbols = result.get("symbols") or []
    if not evidence and not symbols:
        return None

    expected_files = sorted({str(item["file"]) for item in evidence if item.get("file")})
    expected_lines = [
        {"file": str(item["file"]), "line": int(item["line"])}
        for item in evidence
        if item.get("file") and item.get("line") is not None
    ]
    return {
        "id": f"historical_{task['id']}",
        "project": task["project"],
        "question": task["question"],
        "source_task_id": task["id"],
        "review_status": "pending",
        "review_notes": "历史正确反馈仅作为候选，必须人工确认证据和预期结果后才能加入固定评测集。",
        "expected_symbols": sorted(
            {str(item["symbol_name"]) for item in symbols if item.get("symbol_name")}
        ),
        "expected_files": expected_files,
        "expected_queries": list(result.get("queries") or []),
        "expected_lines": expected_lines[:20],
    }


def candidates_from_tasks(tasks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量生成候选并跳过无证据任务。"""
    candidates = []
    for task in tasks:
        candidate = build_candidate(task)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


async def fetch_candidates(
    pool,
    *,
    project: Optional[str] = None,
    days: int = 365,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """查询收到正确反馈的完成任务，并转换为待审核评测候选。"""
    rows = await pool.fetch(
        """
        SELECT t.id, t.project, t.question, t.result, t.updated_at
        FROM diagnostic_tasks AS t
        WHERE t.status = 'COMPLETED'
          AND ($1::text IS NULL OR t.project = $1)
          AND t.updated_at >= NOW() - ($2 * INTERVAL '1 day')
          AND EXISTS (
              SELECT 1 FROM diagnostic_feedback AS f
              WHERE f.task_id = t.id AND f.rating = 'correct'
          )
        ORDER BY t.updated_at DESC
        LIMIT $3
        """,
        project,
        days,
        limit,
    )
    return candidates_from_tasks(dict(row) for row in rows)
