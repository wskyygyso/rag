"""项目发现和项目概览接口。"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Request

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
