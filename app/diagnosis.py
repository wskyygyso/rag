"""诊断任务 HTTP 接口和 SSE 进度流。"""

import asyncio
import json
import uuid
from typing import AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.task_store import get_task, insert_task, mark_cancelled

router = APIRouter(prefix="/api/diagnoses", tags=["diagnoses"])


class CreateDiagnosisRequest(BaseModel):
    """创建诊断任务时的请求参数模型。"""

    question: str = Field(min_length=1, max_length=10000)
    project: str = Field(min_length=1, max_length=100)
    environment: str = Field(default="development", max_length=100)
    created_by: Optional[str] = Field(default=None, max_length=100)


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
    }


def _pool(request: Request):
    """获取应用数据库连接池；依赖不可用时返回明确的 503 错误。"""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return pool


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_diagnosis(payload: CreateDiagnosisRequest, request: Request) -> Dict:
    """创建诊断任务并投递到 Celery 队列。"""
    task_id = f"diag_{uuid.uuid4().hex}"
    task = await insert_task(
        _pool(request),
        task_id,
        payload.project,
        payload.question,
        payload.environment,
        payload.created_by,
    )
    from app.worker import run_diagnosis_task

    run_diagnosis_task.delay(task_id)
    return _task_response(task)


@router.get("/{task_id}")
async def get_diagnosis(task_id: str, request: Request) -> Dict:
    """按任务 ID 查询诊断状态、进度和结果。"""
    task = await get_task(_pool(request), task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    return _task_response(task)


@router.post("/{task_id}/cancel")
async def cancel_diagnosis(task_id: str, request: Request) -> Dict:
    """取消尚未结束的诊断任务。"""
    task = await mark_cancelled(_pool(request), task_id)
    if task is None:
        existing = await get_task(_pool(request), task_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="诊断任务不存在")
        raise HTTPException(status_code=409, detail="任务已经结束，无法取消")
    return _task_response(task)


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
    task = await get_task(_pool(request), task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    return StreamingResponse(
        _events(request, task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
