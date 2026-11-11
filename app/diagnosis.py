"""诊断任务 HTTP 接口和 SSE 进度流。"""

import asyncio
import json
import uuid
from typing import AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.auth import operation_actor, require_project_access
from app.settings import get_settings
from app.task_store import (
    get_task,
    insert_task,
    list_tasks,
    mark_cancelled,
    prepare_task_retry,
    insert_task_operation,
    list_task_operations,
    list_trace_spans,
    update_task,
)
from app.validation_plan import ValidationPlan, validation_plan_dict
from app.reporting import build_assessment, build_evidence_markdown, build_markdown_report

router = APIRouter(prefix="/api/diagnoses", tags=["diagnoses"])
settings = get_settings()


class CreateDiagnosisRequest(BaseModel):
    """创建诊断任务时的请求参数模型。"""

    question: str = Field(min_length=1, max_length=10000)
    project: str = Field(min_length=1, max_length=100)
    environment: str = Field(default="development", max_length=100)
    created_by: Optional[str] = Field(default=None, max_length=100)
    use_ai: Optional[bool] = Field(
        default=None,
        description="true 使用 AI 叙写，false 跳过；null 沿用全局 LLM 配置",
    )
    validation: Optional[ValidationPlan] = Field(
        default=None,
        description="可选的只读数据库、配置和日志验证计划",
    )


def _task_response(task: Dict) -> Dict:
    """把数据库任务记录转换为稳定的 API 响应结构。

    参数:
        - `task`：诊断任务记录，类型为 `Dict`。
    """
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
        "use_ai": task.get("use_ai"),
    }


def _pool(request: Request):
    """获取应用数据库连接池；依赖不可用时返回明确的 503 错误。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
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
    request_id: Optional[str] = None,
) -> None:
    """尽力写入任务操作审计，审计故障不阻断主业务接口。

    参数:
        - `pool`：数据库连接池或测试替身。
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `operation`：操作类型，类型为 `str`。
        - `from_status`：状态变更前值，类型为 `Optional[str]`。
        - `to_status`：状态变更后值，类型为 `Optional[str]`。
        - `actor`：操作主体标识，类型为 `Optional[str]`。
        - `request_id`：HTTP 请求关联 ID，类型为 `Optional[str]`。
    """
    try:
        await insert_task_operation(
            pool,
            task_id,
            operation,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            request_id=request_id,
        )
    except Exception:
        return


async def _authorized_task(pool, request: Request, task_id: str) -> Dict:
    """读取任务并校验项目权限；不存在时统一返回 404，避免泄露任务归属。

    参数:
        - `pool`：数据库连接池或测试替身。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    task = await get_task(pool, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    require_project_access(request, task.get("project"))
    return task


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_diagnosis(payload: CreateDiagnosisRequest, request: Request) -> Dict:
    """创建诊断任务并投递到 Celery 队列。

    参数:
        - `payload`：HTTP 请求体或 LLM 响应载荷，类型为 `CreateDiagnosisRequest`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    pool = _pool(request)
    require_project_access(request, payload.project)
    if payload.use_ai is True and not settings.llm_enabled:
        raise HTTPException(status_code=400, detail="LLM 诊断尚未启用")
    task_id = f"diag_{uuid.uuid4().hex}"
    task = await insert_task(
        pool,
        task_id,
        payload.project,
        payload.question,
        payload.environment,
        payload.created_by,
        validation_plan_dict(payload.validation),
        use_ai=payload.use_ai,
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
        request_id=getattr(request.state, "request_id", None),
    )
    return _task_response(task)


@router.get("/history")
async def diagnosis_history(
    request: Request,
    project: Optional[str] = Query(default=None, max_length=100),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^[A-Z_]{1,32}$"),
    query: Optional[str] = Query(default=None, max_length=200),
    request_id: Optional[str] = Query(default=None, max_length=128),
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict:
    """分页查询历史诊断任务，支持项目、状态、问题关键词和时间范围筛选。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `project`：项目标识，类型为 `Optional[str]`。
        - `status_filter`：诊断任务状态筛选值，类型为 `Optional[str]`。
        - `query`：检索或查询文本，类型为 `Optional[str]`。
        - `request_id`：HTTP 请求关联 ID，类型为 `Optional[str]`。
        - `days`：最近天数范围，类型为 `Optional[int]`。
        - `offset`：分页起始偏移，类型为 `int`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    require_project_access(request, project)
    tasks = await list_tasks(
        _pool(request),
        project=project,
        status=status_filter,
        query=query,
        request_id=request_id,
        days=days,
        offset=offset,
        limit=limit,
    )
    return {"items": tasks, "offset": offset, "limit": limit}


@router.get("/{task_id}")
async def get_diagnosis(task_id: str, request: Request) -> Dict:
    """按任务 ID 查询诊断状态、进度和结果。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    task = await _authorized_task(_pool(request), request, task_id)
    return _task_response(task)


@router.post("/{task_id}/cancel")
async def cancel_diagnosis(task_id: str, request: Request) -> Dict:
    """取消尚未结束的诊断任务。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
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
        request_id=getattr(request.state, "request_id", None),
    )
    return _task_response(task)


@router.get("/{task_id}/operations")
async def task_operations(
    task_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> Dict:
    """查询诊断任务操作审计轨迹，供协作排查使用。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    return {"task_id": task_id, "operations": await list_task_operations(pool, task_id, limit)}


@router.post("/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_diagnosis(task_id: str, request: Request) -> Dict:
    """将失败任务重新排队；非失败任务或达到上限时拒绝重试。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
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
        request_id=getattr(request.state, "request_id", None),
    )
    return _task_response(task)


@router.post("/{task_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def rerun_diagnosis(task_id: str, request: Request) -> Dict:
    """复制历史任务配置并创建一个新的诊断任务。

    参数:
        - `task_id`：需要复制问题、环境、验证计划和 AI 开关的历史任务 ID。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
    """
    pool = _pool(request)
    source = await _authorized_task(pool, request, task_id)
    new_task_id = f"diag_{uuid.uuid4().hex}"
    task = await insert_task(
        pool,
        new_task_id,
        source["project"],
        source["question"],
        source["environment"],
        source.get("created_by"),
        source.get("validation_plan"),
        use_ai=source.get("use_ai"),
        request_id=getattr(request.state, "request_id", None),
    )
    from app.worker import run_diagnosis_task

    try:
        run_diagnosis_task.delay(new_task_id)
    except Exception as exc:
        await update_task(
            pool,
            new_task_id,
            status="FAILED",
            progress=100,
            current_step="重新诊断任务投递失败",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail="重新诊断任务投递失败") from exc
    await _record_operation(
        pool,
        new_task_id,
        "RERUN_CREATE",
        to_status=task.get("status"),
        actor=operation_actor(request),
        request_id=getattr(request.state, "request_id", None),
    )
    return _task_response(task)


@router.get("/{task_id}/report")
async def get_report(
    task_id: str,
    request: Request,
    format: str = Query(default="json", pattern="^(json|markdown|evidence)$"),
):
    """获取稳定诊断报告，支持 JSON、完整报告和证据文件导出。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `format`：报告导出格式；`json` 供前端结构化阅读，`markdown` 导出完整
          报告，`evidence` 导出业务限制与源码上下文，类型为 `str`。
    """
    task = await _authorized_task(_pool(request), request, task_id)
    response = _task_response(task)
    response["assessment"] = build_assessment(response.get("result") or {})
    if format == "markdown":
        return PlainTextResponse(
            build_markdown_report(response),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{task_id}.md"'},
        )
    if format == "evidence":
        return PlainTextResponse(
            build_evidence_markdown(response),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{task_id}-evidence.md"'},
        )
    return response


@router.get("/{task_id}/trace")
async def get_diagnosis_trace(
    task_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
) -> Dict:
    """返回任务阶段时间线、耗时和脱敏降级统计。

    参数:
        - `task_id`：需要读取轨迹的诊断任务 ID。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `limit`：单次最多返回的轨迹数量；大任务可分页读取完整时间线。
    """
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    spans = await list_trace_spans(pool, task_id, limit=limit)
    elapsed_values = [
        float(item["elapsed_ms"])
        for item in spans
        if item.get("elapsed_ms") is not None
    ]
    return {
        "task_id": task_id,
        "count": len(spans),
        "summary": {
            "failed_count": sum(item.get("status") == "failed" for item in spans),
            "degraded_count": sum(
                item.get("status") in {"degraded", "insufficient_support"}
                for item in spans
            ),
            "max_elapsed_ms": round(max(elapsed_values), 3) if elapsed_values else 0,
        },
        "items": spans,
    }


async def _events(request: Request, task_id: str) -> AsyncIterator[str]:
    """轮询任务状态并生成 SSE 格式的进度事件。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
    last_trace_id = 0
    while True:
        if await request.is_disconnected():
            return
        pool = _pool(request)
        try:
            traces = await list_trace_spans(
                pool,
                task_id,
                after_id=last_trace_id,
                limit=100,
            )
        except Exception:
            # 轨迹只是可观测性增强；读取失败时继续发送任务状态，避免页面失联。
            traces = []
        for trace in traces:
            last_trace_id = max(last_trace_id, int(trace["id"]))
            payload = json.dumps(trace, ensure_ascii=False)
            yield f"id: trace-{last_trace_id}\nevent: trace\ndata: {payload}\n\n"
        task = await get_task(pool, task_id)
        if task is None:
            yield "event: error\ndata: {\"detail\":\"诊断任务不存在\"}\n\n"
            return
        payload = json.dumps(_task_response(task), ensure_ascii=False)
        yield f"id: task-{task.get('updated_at', '')}\nevent: progress\ndata: {payload}\n\n"
        if task["status"] in terminal_statuses:
            return
        await asyncio.sleep(0.5)


@router.get("/{task_id}/events")
async def diagnosis_events(task_id: str, request: Request) -> StreamingResponse:
    """建立诊断任务的实时进度 SSE 响应。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    await _authorized_task(_pool(request), request, task_id)
    return StreamingResponse(
        _events(request, task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
