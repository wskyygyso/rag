"""代码符号和调用链只读查询接口。"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.auth import require_project_access
from app.code_tools import CodeToolError
from app.settings import get_settings
from app.symbol_tools import find_references, find_symbol, trace_call_chain

router = APIRouter(prefix="/api/code", tags=["code-analysis"])
settings = get_settings()


def _tool_error(exc: Exception) -> HTTPException:
    """将代码工具的参数或仓库错误转换为 HTTP 400 响应。"""
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/{project}/symbols")
async def get_symbol(
    project: str,
    request: Request,
    name: str = Query(min_length=1, max_length=255),
) -> Dict:
    """查询项目中的类、方法或函数定义。"""
    require_project_access(request, project)
    try:
        return await run_in_threadpool(
            find_symbol,
            settings.code_repositories_root,
            project,
            name,
        )
    except (CodeToolError, ValueError) as exc:
        raise _tool_error(exc) from exc


@router.get("/{project}/references")
async def get_references(
    project: str,
    request: Request,
    symbol: str = Query(min_length=1, max_length=255),
) -> Dict:
    """查询项目中对指定符号的调用引用。"""
    require_project_access(request, project)
    try:
        return await run_in_threadpool(
            find_references,
            settings.code_repositories_root,
            project,
            symbol,
        )
    except (CodeToolError, ValueError) as exc:
        raise _tool_error(exc) from exc


@router.get("/{project}/call-chain")
async def get_call_chain(
    project: str,
    request: Request,
    entry: str = Query(min_length=1, max_length=255),
    depth: int = Query(default=5, ge=1, le=10),
) -> Dict:
    """从指定入口符号展开项目静态调用链。"""
    require_project_access(request, project)
    try:
        return await run_in_threadpool(
            trace_call_chain,
            settings.code_repositories_root,
            project,
            entry,
            depth,
        )
    except (CodeToolError, ValueError) as exc:
        raise _tool_error(exc) from exc
