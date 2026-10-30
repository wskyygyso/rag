"""混合检索 HTTP 接口。"""

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import require_project_access
from app.code_tools import CodeToolError
from app.hybrid_search import search_hybrid
from app.settings import get_settings

router = APIRouter(prefix="/api/search", tags=["hybrid-search"])
settings = get_settings()


@router.get("/{project}/hybrid")
async def hybrid_search(
    project: str,
    request: Request,
    query: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    history_offset: int = Query(default=0, ge=0, le=10_000),
    history_days: Optional[int] = Query(default=None, ge=1, le=3650),
    source_types: Optional[list[str]] = Query(
        default=None,
        description="重复传入以筛选来源：code、symbol、chunk 或 history",
    ),
) -> Dict:
    """合并代码、符号和历史诊断结果，返回带来源和分数的检索结果。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `query`：检索或查询文本，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
        - `history_offset`：历史诊断结果分页偏移，类型为 `int`。
        - `history_days`：历史诊断结果最近天数范围，类型为 `Optional[int]`。
        - `source_types`：混合检索来源筛选类型，类型为 `Optional[list[str]]`。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    try:
        return await search_hybrid(
            settings.code_repositories_root,
            project,
            query,
            pool,
            limit,
            history_offset,
            history_days,
            source_types,
        )
    except (CodeToolError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
