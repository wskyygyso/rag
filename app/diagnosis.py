"""诊断任务 HTTP 接口和 SSE 进度流。"""

import asyncio
import json
import uuid
from typing import AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.auth import operation_actor, require_project_access
from app.task_store import (
    get_task,
    insert_task,
    list_tasks,
    mark_cancelled,
    prepare_task_retry,
    insert_task_operation,
    list_task_operations,
    update_task,
)
from app.validation_plan import ValidationPlan, validation_plan_dict
from app.reporting import build_assessment, build_markdown_report

router = APIRouter(prefix="/api/diagnoses", tags=["diagnoses"])


class CreateDiagnosisRequest(BaseModel):
    """创建诊断任务时的请求参数模型。"""

    question: str = Field(min_length=1, max_length=10000)
    project: str = Field(min_length=1, max_length=100)
    environment: str = Field(default="development", max_length=100)
    created_by: Optional[str] = Field(default=None, max_length=100)
    validation: Optional[ValidationPlan] = Field(
        default=None,
        description="可选的只读数据库、配置和日志验证计划",
    )


def _task_response(task: Dict) -> Dict:
    """把数据库任务记录转换为稳定的 API 响应结构。"""
    return {
        "task_id": task["id"],
        "project": task["project"],
        "question": task["question"],
        "environment": task["environment"],
        "status": task["status"],
        "progress": task["progress"],
        "current_step": task["current_step"],
        "result": task["result"],
        "error": task["error"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "request_id": task.get("request_id"),
    }


def _pool(request: Request):
    """获取应用数据库连接池；依赖不可用时返回明确的 503 错误。"""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return pool


async def _record_operation(
    pool,
    task_id: str,
    operation: str,
    *,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    actor: Optional[str] = None,
) -> None:
    """尽力写入任务操作审计，审计故障不阻断主业务接口。"""
    try:
        await insert_task_operation(
            pool,
            task_id,
            operation,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
        )
    except Exception:
        return


async def _authorized_task(pool, request: Request, task_id: str) -> Dict:
    """读取任务并校验项目权限；不存在时统一返回 404，避免泄露任务归属。"""
    task = await get_task(pool, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    require_project_access(request, task.get("project"))
    return task


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_diagnosis(payload: CreateDiagnosisRequest, request: Request) -> Dict:
    """创建诊断任务并投递到 Celery 队列。"""
    pool = _pool(request)
    require_project_access(request, payload.project)
    task_id = f"diag_{uuid.uuid4().hex}"
    task = await insert_task(
        pool,
        task_id,
        payload.project,
        payload.question,
        payload.environment,
        payload.created_by,
        validation_plan_dict(payload.validation),
        request_id=getattr(request.state, "request_id", None),
    )
    from app.worker import run_diagnosis_task

    try:
        run_diagnosis_task.delay(task_id)
    except Exception as exc:
        await update_task(
            pool,
            task_id,
            status="FAILED",
            progress=100,
            current_step="任务重新投递失败",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail="任务重新投递失败") from exc
    await _record_operation(
        pool,
        task_id,
        "CREATE",
        to_status=task.get("status"),
        actor=operation_actor(request, payload.created_by),
    )
    return _task_response(task)


@router.get("/history")
async def diagnosis_history(
    request: Request,
    project: Optional[str] = Query(default=None, max_length=100),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^[A-Z_]{1,32}$"),
    query: Optional[str] = Query(default=None, max_length=200),
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict:
    """分页查询历史诊断任务，支持项目、状态、问题关键词和时间范围筛选。"""
    require_project_access(request, project)
    tasks = await list_tasks(
        _pool(request),
        project=project,
        status=status_filter,
        query=query,
        days=days,
        offset=offset,
        limit=limit,
    )
    return {"items": tasks, "offset": offset, "limit": limit}


@router.get("/{task_id}")
async def get_diagnosis(task_id: str, request: Request) -> Dict:
    """按任务 ID 查询诊断状态、进度和结果。"""
    task = await _authorized_task(_pool(request), request, task_id)
    return _task_response(task)


@router.post("/{task_id}/cancel")
async def cancel_diagnosis(task_id: str, request: Request) -> Dict:
    """取消尚未结束的诊断任务。"""
    pool = _pool(request)
    previous = await _authorized_task(pool, request, task_id)
    task = await mark_cancelled(pool, task_id)
    if task is None:
        raise HTTPException(status_code=409, detail="任务已经结束，无法取消")
    await _record_operation(
        pool,
        task_id,
        "CANCEL",
        from_status=previous.get("status") if previous else None,
        to_status=task.get("status"),
        actor=operation_actor(request),
    )
    return _task_response(task)


@router.get("/{task_id}/operations")
async def task_operations(
    task_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> Dict:
    """查询诊断任务操作审计轨迹，供协作排查使用。"""
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    return {"task_id": task_id, "operations": await list_task_operations(pool, task_id, limit)}


@router.post("/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_diagnosis(task_id: str, request: Request) -> Dict:
    """将失败任务重新排队；非失败任务或达到上限时拒绝重试。"""
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    task = await prepare_task_retry(pool, task_id)
    if task is None:
        existing = await get_task(pool, task_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="诊断任务不存在")
        if existing.get("retry_count", 0) >= 3:
            raise HTTPException(status_code=409, detail="任务已达到人工重试上限")
        raise HTTPException(status_code=409, detail="只有失败任务可以重新执行")
    from app.worker import run_diagnosis_task

    try:
        run_diagnosis_task.delay(task_id)
    except Exception as exc:
        await update_task(
            pool,
            task_id,
            status="FAILED",
            progress=100,
            current_step="任务重新投递失败",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail="任务重新投递失败") from exc
    await _record_operation(
        pool,
        task_id,
        "RETRY",
        from_status="FAILED",
        to_status=task.get("status"),
        actor=operation_actor(request),
    )
    return _task_response(task)


@router.get("/{task_id}/report")
async def get_report(
    task_id: str,
    request: Request,
    format: str = Query(default="json", pattern="^(json|markdown)$"),
):
    """获取稳定诊断报告，支持 JSON 摘要或 Markdown 导出。"""
    task = await _authorized_task(_pool(request), request, task_id)
    response = _task_response(task)
    response["assessment"] = build_assessment(response.get("result") or {})
    if format == "markdown":
        return PlainTextResponse(
            build_markdown_report(response),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{task_id}.md"'},
        )
    return response


async def _events(request: Request, task_id: str) -> AsyncIterator[str]:
    """轮询任务状态并生成 SSE 格式的进度事件。"""
    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
    while True:
        if await request.is_disconnected():
            return
        task = await get_task(_pool(request), task_id)
        if task is None:
            yield "event: error\ndata: {\"detail\":\"诊断任务不存在\"}\n\n"
            return
        payload = json.dumps(_task_response(task), ensure_ascii=False)
        yield f"event: progress\ndata: {payload}\n\n"
        if task["status"] in terminal_statuses:
            return
        await asyncio.sleep(0.5)


@router.get("/{task_id}/events")
async def diagnosis_events(task_id: str, request: Request) -> StreamingResponse:
    """建立诊断任务的实时进度 SSE 响应。"""
    await _authorized_task(_pool(request), request, task_id)
    return StreamingResponse(
        _events(request, task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
