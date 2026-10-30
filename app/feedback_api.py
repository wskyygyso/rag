"""诊断报告人工反馈接口。"""

from typing import Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.auth import operation_actor, require_project_access
from app.task_store import (
    get_task,
    insert_feedback,
    list_feedback,
    summarize_feedback,
    summarize_project_feedback,
)

router = APIRouter(prefix="/api/diagnoses", tags=["diagnosis-feedback"])


class FeedbackRequest(BaseModel):
    """人工反馈请求参数，限制反馈类型、说明长度和提交者标识。"""

    rating: Literal["correct", "inaccurate", "insufficient_evidence", "escalate"]
    comment: Optional[str] = Field(default=None, max_length=2000)
    submitted_by: Optional[str] = Field(default=None, max_length=100)


def _pool(request: Request):
    """获取数据库连接池，未初始化时返回统一的服务不可用错误。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return pool


async def _authorized_task(pool, request: Request, task_id: str) -> Dict:
    """读取诊断任务并校验当前调用方的项目访问权限。

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


@router.post("/{task_id}/feedback", status_code=status.HTTP_201_CREATED)
async def create_feedback(
    task_id: str, payload: FeedbackRequest, request: Request
) -> Dict:
    """为已有诊断任务保存一条人工反馈。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `payload`：HTTP 请求体或 LLM 响应载荷，类型为 `FeedbackRequest`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    return await insert_feedback(
        pool,
        task_id,
        payload.rating,
        payload.comment,
        operation_actor(request, payload.submitted_by),
    )


@router.get("/{task_id}/feedback")
async def get_feedback(
    task_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> Dict:
    """查询诊断任务最近的人工反馈，支持分页上限控制。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    return {"task_id": task_id, "feedback": await list_feedback(pool, task_id, limit)}


@router.get("/{task_id}/feedback/summary")
async def get_feedback_summary(task_id: str, request: Request) -> Dict:
    """返回任务反馈分布和最近反馈时间，便于协作质量统计。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    pool = _pool(request)
    await _authorized_task(pool, request, task_id)
    return {"task_id": task_id, **await summarize_feedback(pool, task_id)}


project_feedback_router = APIRouter(prefix="/api/feedback", tags=["diagnosis-feedback"])


@project_feedback_router.get("/{project}/summary")
async def get_project_feedback_summary(project: str, request: Request) -> Dict:
    """返回项目范围内的反馈质量汇总，支持质量看板使用。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    pool = _pool(request)
    require_project_access(request, project)
    return {"project": project, **await summarize_project_feedback(pool, project)}
