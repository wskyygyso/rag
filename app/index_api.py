"""文档索引运行状态查询接口。"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/index", tags=["index"])


@router.get("/{project}/runs")
async def index_runs(
    project: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict:
    """返回项目最近的文档索引运行状态和版本信息。"""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    rows = await pool.fetch(
        """
        SELECT id, project, status, chunk_count, repository_revision, index_version,
               error, started_at, finished_at
        FROM document_index_runs
        WHERE project = $1
        ORDER BY started_at DESC
        LIMIT $2
        """,
        project,
        limit,
    )
    return {
        "project": project,
        "runs": [
            {
                **dict(row),
                "started_at": row["started_at"].isoformat(),
                "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
            }
            for row in rows
        ],
    }
