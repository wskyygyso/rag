"""项目发现和项目概览接口。"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Request, status

from app.auth import api_identity, require_project_access
from app.projects import discover_projects, get_project_overview, read_repository_revision
from app.settings import get_settings

router = APIRouter(prefix="/api/projects", tags=["projects"])
settings = get_settings()


@router.get("")
async def list_projects(request: Request) -> Dict:
    """返回调用方有权访问的项目目录及 Git 版本。

    参数:
        - `request`：携带认证身份和项目根目录配置的 FastAPI 请求。
    """
    projects = discover_projects(settings.code_repositories_root, api_identity(request))
    return {"projects": projects}


@router.get("/{project}/overview")
async def project_overview(project: str, request: Request) -> Dict:
    """返回项目任务、反馈和最近索引运行概览。

    参数:
        - `project`：需要查看概览的诊断项目标识。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    overview = await get_project_overview(pool, project)
    overview["repository_revision"] = read_repository_revision(
        settings.code_repositories_root, project
    )
    return overview


@router.post("/{project}/index", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_project_index(project: str, request: Request) -> Dict:
    """将项目文档索引重建任务投递到独立 index 队列。

    参数:
        - `project`：需要重建索引的诊断项目标识。
        - `request`：携带认证身份、数据库池和队列入口的 FastAPI 请求。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    running = await pool.fetchrow(
        """
        SELECT id FROM document_index_runs
        WHERE project = $1 AND status = 'RUNNING'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        project,
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="项目索引正在运行")
    task_entry = globals().get("run_document_index_task")
    if task_entry is None:
        from app.worker import run_document_index_task as task_entry
    queued = task_entry.delay(project)
    return {
        "project": project,
        "status": "QUEUED",
        "task_id": queued.id,
    }
