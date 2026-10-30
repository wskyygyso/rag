"""文档索引运行状态查询接口。"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import require_project_access

router = APIRouter(prefix="/api/index", tags=["index"])


@router.get("/{project}/runs")
async def index_runs(
    project: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict:
    """返回项目最近的文档索引运行状态和版本信息。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    require_project_access(request, project)
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


@router.post("/{project}/runs/{run_id}/retry", status_code=202)
async def retry_index_run(project: str, run_id: int, request: Request) -> Dict:
    """将失败索引重新投递到 Worker，避免 API 进程阻塞执行。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `run_id`：运行记录 ID，类型为 `int`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    row = await pool.fetchrow(
        "SELECT id, status FROM document_index_runs WHERE id = $1 AND project = $2",
        run_id,
        project,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="索引运行记录不存在")
    if row["status"] != "FAILED":
        raise HTTPException(status_code=409, detail="只有失败的索引运行可以重试")
    task_entry = globals().get("run_document_index_task")
    if task_entry is None:
        from app.worker import run_document_index_task as task_entry

    queued = task_entry.delay(project)
    return {"project": project, "run_id": run_id, "status": "QUEUED", "task_id": queued.id}
