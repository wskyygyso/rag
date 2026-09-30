"""诊断报告人工反馈接口。"""

from typing import Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.task_store import get_task, insert_feedback, list_feedback

router = APIRouter(prefix="/api/diagnoses", tags=["diagnosis-feedback"])


class FeedbackRequest(BaseModel):
    """人工反馈请求参数，限制反馈类型、说明长度和提交者标识。"""

    rating: Literal["correct", "inaccurate", "insufficient_evidence", "escalate"]
    comment: Optional[str] = Field(default=None, max_length=2000)
    submitted_by: Optional[str] = Field(default=None, max_length=100)


def _pool(request: Request):
    """获取数据库连接池，未初始化时返回统一的服务不可用错误。"""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return pool


@router.post("/{task_id}/feedback", status_code=status.HTTP_201_CREATED)
async def create_feedback(
    task_id: str, payload: FeedbackRequest, request: Request
) -> Dict:
    """为已有诊断任务保存一条人工反馈。"""
    pool = _pool(request)
    if await get_task(pool, task_id) is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    return await insert_feedback(
        pool,
        task_id,
        payload.rating,
        payload.comment,
        payload.submitted_by,
    )


@router.get("/{task_id}/feedback")
async def get_feedback(
    task_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> Dict:
    """查询诊断任务最近的人工反馈，支持分页上限控制。"""
    pool = _pool(request)
    if await get_task(pool, task_id) is None:
        raise HTTPException(status_code=404, detail="诊断任务不存在")
    return {"task_id": task_id, "feedback": await list_feedback(pool, task_id, limit)}
