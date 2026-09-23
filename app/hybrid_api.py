"""混合检索 HTTP 接口。"""

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
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
) -> Dict:
    """合并代码、符号和历史诊断结果，返回带来源和分数的检索结果。"""
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
        )
    except (CodeToolError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
