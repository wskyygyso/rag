"""AI 诊断质量和成本看板接口。"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Query, Request

from app.ai_quality import summarize_ai_quality
from app.auth import require_project_access

router = APIRouter(prefix="/api/ai", tags=["ai-quality"])


@router.get("/{project}/quality")
async def ai_quality(
    project: str,
    request: Request,
    days: int = Query(default=30, ge=1, le=3650),
) -> Dict:
    """返回项目近期 AI 调用、缓存、token 和人工反馈摘要。

    参数:
        - `project`：需要查询质量指标的诊断项目。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `days`：统计最近多少天的已完成诊断任务。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    rows = await pool.fetch(
        """
        SELECT t.result,
               COUNT(f.id) FILTER (WHERE f.rating = 'correct') AS correct_feedback,
               COUNT(f.id) FILTER (WHERE f.rating = 'inaccurate') AS inaccurate_feedback
        FROM diagnostic_tasks AS t
        LEFT JOIN diagnostic_feedback AS f ON f.task_id = t.id
        WHERE t.project = $1
          AND t.status = 'COMPLETED'
          AND t.updated_at >= NOW() - ($2 * INTERVAL '1 day')
        GROUP BY t.id, t.result, t.updated_at
        ORDER BY t.updated_at DESC
        LIMIT 2000
        """,
        project,
        days,
    )
    return {
        "project": project,
        "days": days,
        **summarize_ai_quality(dict(row) for row in rows),
    }
