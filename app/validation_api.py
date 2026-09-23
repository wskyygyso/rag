"""数据库、配置和日志验证的只读 HTTP 接口。"""

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.code_tools import CodeToolError
from app.settings import get_settings
from app.rate_limit import RateLimiter
from app.validation_tools import (
    ValidationToolError,
    query_database,
    record_validation_audit,
    read_config,
    search_logs,
)

router = APIRouter(prefix="/api/validation", tags=["validation"])
settings = get_settings()
logger = logging.getLogger(__name__)
rate_limiter = RateLimiter(
    settings.validation_rate_limit_per_minute,
    settings.validation_rate_limit_window_seconds,
)


class DatabaseQueryRequest(BaseModel):
    """只读数据库查询请求参数。"""

    sql: str = Field(min_length=1, max_length=10000)
    params: Optional[List[Any]] = Field(default=None, max_length=20)
    max_rows: int = Field(default=100, ge=1, le=1000)
    max_result_bytes: int = Field(default=512_000, ge=1, le=5_000_000)
    task_id: Optional[str] = Field(default=None, max_length=64)
    project: Optional[str] = Field(default=None, max_length=100)


class ConfigReadRequest(BaseModel):
    """配置文件读取请求参数。"""

    project: str = Field(min_length=1, max_length=100)
    file: str = Field(min_length=1, max_length=500)
    task_id: Optional[str] = Field(default=None, max_length=64)


class LogSearchRequest(BaseModel):
    """日志关键词查询请求参数。"""

    project: str = Field(min_length=1, max_length=100)
    file: str = Field(min_length=1, max_length=500)
    query: str = Field(min_length=1, max_length=200)
    max_results: int = Field(default=100, ge=1, le=1000)
    task_id: Optional[str] = Field(default=None, max_length=64)


async def _audit(
    request: Request,
    operation: str,
    status: str,
    *,
    task_id: Optional[str] = None,
    project: Optional[str] = None,
    request_summary: Optional[dict] = None,
    result_summary: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    """向共享连接池写入审计；数据库不可用时跳过审计而不阻断验证接口。"""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return
    await record_validation_audit(
        pool,
        operation,
        status,
        task_id=task_id,
        project=project,
        request_summary=request_summary,
        result_summary=result_summary,
        error=error,
    )


def _client_key(request: Request) -> str:
    """获取限流调用方标识；未连接客户端时使用统一匿名键。"""
    return request.client.host if request.client else "anonymous"


def _check_rate_limit(request: Request) -> None:
    """检查验证接口限流，超限时返回带重试提示的 429。"""
    if not rate_limiter.allow(_client_key(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="验证接口请求过于频繁，请稍后重试",
            headers={"Retry-After": str(settings.validation_rate_limit_window_seconds)},
        )


@router.post("/database/query")
async def database_query(payload: DatabaseQueryRequest, request: Request) -> dict:
    """执行只读 SQL，并记录查询审计摘要。"""
    _check_rate_limit(request)
    try:
        result = await query_database(
            settings,
            payload.sql,
            payload.params,
            payload.max_rows,
            max_result_bytes=payload.max_result_bytes,
        )
        await _audit(
            request,
            "database_query",
            "SUCCESS",
            task_id=payload.task_id,
            project=payload.project,
            request_summary={
                "max_rows": payload.max_rows,
                "max_result_bytes": payload.max_result_bytes,
                "param_count": len(payload.params or []),
            },
            result_summary={"row_count": result["total"]},
        )
        logger.info("database_validation_query sql=%s rows=%s", result["sql"], result["total"])
        return result
    except ValidationToolError as exc:
        await _audit(
            request,
            "database_query",
            "FAILED",
            task_id=payload.task_id,
            project=payload.project,
            request_summary={
                "max_rows": payload.max_rows,
                "max_result_bytes": payload.max_result_bytes,
                "param_count": len(payload.params or []),
            },
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/config/read")
async def config_read(payload: ConfigReadRequest, request: Request) -> dict:
    """读取项目配置文件，返回已脱敏内容。"""
    _check_rate_limit(request)
    try:
        result = read_config(settings.code_repositories_root, payload.project, payload.file)
        await _audit(
            request,
            "config_read",
            "SUCCESS",
            task_id=payload.task_id,
            project=payload.project,
            request_summary={"file": payload.file},
            result_summary={"content_length": len(result["content"])},
        )
        return result
    except (ValidationToolError, CodeToolError) as exc:
        await _audit(
            request,
            "config_read",
            "FAILED",
            task_id=payload.task_id,
            project=payload.project,
            request_summary={"file": payload.file},
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/logs/search")
async def logs_search(payload: LogSearchRequest, request: Request) -> dict:
    """检索项目日志关键词，返回已脱敏的匹配行。"""
    _check_rate_limit(request)
    try:
        result = search_logs(
            settings.code_repositories_root,
            payload.project,
            payload.file,
            payload.query,
            payload.max_results,
        )
        await _audit(
            request,
            "logs_search",
            "SUCCESS",
            task_id=payload.task_id,
            project=payload.project,
            request_summary={"file": payload.file, "max_results": payload.max_results},
            result_summary={"match_count": len(result["matches"])},
        )
        return result
    except (ValidationToolError, CodeToolError) as exc:
        await _audit(
            request,
            "logs_search",
            "FAILED",
            task_id=payload.task_id,
            project=payload.project,
            request_summary={"file": payload.file, "max_results": payload.max_results},
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
