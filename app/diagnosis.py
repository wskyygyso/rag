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
    question: str = Field(min_length=1, max_length=10000)
    project: str = Field(min_length=1, max_length=100)
    environment: str = Field(default="development", max_length=100)
    created_by: Optional[str] = Field(default=None, max_length=100)


def _task_response(task: Dict) -> Dict:
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
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return pool


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_diagnosis(payload: CreateDiagnosisRequest, request: Request) -> Dict:
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
    task = await get_task(_pool(request), task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    return _task_response(task)


@router.post("/{task_id}/cancel")
async def cancel_diagnosis(task_id: str, request: Request) -> Dict:
    task = await mark_cancelled(_pool(request), task_id)
    if task is None:
        existing = await get_task(_pool(request), task_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="诊断任务不存在")
        raise HTTPException(status_code=409, detail="任务已经结束，无法取消")
    return _task_response(task)


async def _events(request: Request, task_id: str) -> AsyncIterator[str]:
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
    task = await get_task(_pool(request), task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    return StreamingResponse(
        _events(request, task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
