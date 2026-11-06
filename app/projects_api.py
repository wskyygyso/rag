"""项目发现和项目概览接口。"""

from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import api_identity, operation_actor, require_project_access
from app.projects import (
    discover_projects,
    get_project_overview,
    get_project_task_board,
    read_repository_revision,
)
from app.settings import get_settings
from app.task_store import (
    insert_task_operation,
    mark_cancelled_many,
    prepare_task_retry,
    update_task,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])
settings = get_settings()
MAX_BULK_TASK_COUNT = 20


class BulkTaskRequest(BaseModel):
    """项目任务看板批量操作的请求参数。"""

    task_ids: List[str] = Field(min_length=1, max_length=MAX_BULK_TASK_COUNT)


def _bulk_task_ids(payload: BulkTaskRequest) -> List[str]:
    """按首次出现顺序去重批量操作任务 ID。

    参数:
        - `payload`：已通过数量和非空校验的批量操作请求。

    返回:
        去重后的任务 ID 列表；保持调用方提交顺序，便于结果稳定呈现。
    """
    return list(dict.fromkeys(payload.task_ids))


def _operation_detail(task_id: str, outcome: str, reason: str = "") -> Dict:
    """构造批量操作的单任务结果，供页面展示和接口调用方消费。

    参数:
        - `task_id`：本次批量操作涉及的诊断任务 ID。
        - `outcome`：单任务处理结果，取值为 `cancelled`、`retried` 或 `skipped`。
        - `reason`：跳过或失败的业务原因；成功时为空字符串。
    """
    return {"task_id": task_id, "outcome": outcome, "reason": reason}


@router.get("")
async def list_projects(request: Request) -> Dict:
    """返回调用方有权访问的项目目录及 Git 版本。

    参数:
        - `request`：携带认证身份和项目根目录配置的 FastAPI 请求。
    """
    projects = discover_projects(settings.code_repositories_root, api_identity(request))
    return {"projects": projects}


@router.get("/{project}/overview")
async def project_overview(project: str, request: Request) -> Dict:
    """返回项目任务、反馈和最近索引运行概览。

    参数:
        - `project`：需要查看概览的诊断项目标识。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    overview = await get_project_overview(pool, project)
    overview["repository_revision"] = read_repository_revision(
        settings.code_repositories_root, project
    )
    return overview


@router.post("/{project}/index", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_project_index(project: str, request: Request) -> Dict:
    """将项目文档索引重建任务投递到独立 index 队列。

    参数:
        - `project`：需要重建索引的诊断项目标识。
        - `request`：携带认证身份、数据库池和队列入口的 FastAPI 请求。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    running = await pool.fetchrow(
        """
        SELECT id FROM document_index_runs
        WHERE project = $1 AND status = 'RUNNING'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        project,
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="项目索引正在运行")
    task_entry = globals().get("run_document_index_task")
    if task_entry is None:
        from app.worker import run_document_index_task as task_entry
    queued = task_entry.delay(project)
    return {
        "project": project,
        "status": "QUEUED",
        "task_id": queued.id,
    }


@router.get("/{project}/task-board")
async def project_task_board(project: str, request: Request) -> Dict:
    """返回项目状态统计、运行中任务和最近终态任务。

    参数:
        - `project`：需要查看任务看板的诊断项目标识。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return await get_project_task_board(pool, project)


@router.post("/{project}/tasks/cancel")
async def cancel_project_tasks(
    project: str,
    payload: BulkTaskRequest,
    request: Request,
) -> Dict:
    """批量取消项目内尚未结束的诊断任务。

    参数:
        - `project`：任务所属的诊断项目标识。
        - `payload`：需要取消的任务 ID 集合，最多 20 个。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    task_ids = _bulk_task_ids(payload)
    before_rows = await pool.fetch(
        "SELECT id, status FROM diagnostic_tasks WHERE project = $1 AND id = ANY($2::varchar[])",
        project,
        task_ids,
    )
    previous = {str(row["id"]): str(row["status"]) for row in before_rows}
    cancelled_rows = await mark_cancelled_many(pool, project, task_ids)
    cancelled_ids = [str(row["id"]) for row in cancelled_rows]
    skipped = [
        {
            "task_id": task_id,
            "reason": "任务不存在或不属于当前项目" if task_id not in previous else "任务已经结束",
        }
        for task_id in task_ids
        if task_id not in cancelled_ids
    ]
    request_id = getattr(request.state, "request_id", None)
    for task_id in cancelled_ids:
        try:
            await insert_task_operation(
                pool,
                task_id,
                "BULK_CANCEL",
                from_status=previous.get(task_id),
                to_status="CANCELLED",
                actor=operation_actor(request),
                request_id=request_id,
                detail={"source": "project-task-board", "batch_size": len(task_ids)},
            )
        except Exception:
            continue
    details = [_operation_detail(task_id, "cancelled") for task_id in cancelled_ids]
    details.extend(
        _operation_detail(item["task_id"], "skipped", item["reason"]) for item in skipped
    )
    return {
        "project": project,
        "cancelled": cancelled_ids,
        "skipped": skipped,
        "details": details,
        "request_id": request_id,
    }


@router.post("/{project}/tasks/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_project_tasks(
    project: str,
    payload: BulkTaskRequest,
    request: Request,
) -> Dict:
    """批量重新投递项目内未超过人工重试上限的失败任务。

    参数:
        - `project`：任务所属的诊断项目标识，调用方必须具备写权限。
        - `payload`：需要重试的任务 ID 集合，单次最多 20 个。
        - `request`：携带认证身份、请求 ID 和数据库连接池的 FastAPI 请求。

    返回:
        包含成功重试任务、跳过任务、逐任务结果明细和请求关联 ID 的摘要。
        部分任务失败时仍返回 202，由 `skipped` 明确说明未重试原因。
    """
    require_project_access(request, project)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    task_ids = _bulk_task_ids(payload)
    before_rows = await pool.fetch(
        """
        SELECT id, status, retry_count
        FROM diagnostic_tasks
        WHERE project = $1 AND id = ANY($2::varchar[])
        """,
        project,
        task_ids,
    )
    previous = {str(row["id"]): row for row in before_rows}
    retried_ids: List[str] = []
    skipped: List[Dict] = []
    request_id = getattr(request.state, "request_id", None)
    from app.worker import run_diagnosis_task

    for task_id in task_ids:
        task_row = previous.get(task_id)
        if task_row is None:
            skipped.append(
                {"task_id": task_id, "reason": "任务不存在或不属于当前项目"}
            )
            continue
        if str(task_row["status"]) != "FAILED":
            skipped.append({"task_id": task_id, "reason": "只有失败任务可以重新执行"})
            continue
        if int(task_row.get("retry_count") or 0) >= 3:
            skipped.append({"task_id": task_id, "reason": "任务已达到人工重试上限"})
            continue
        task = await prepare_task_retry(pool, task_id)
        if task is None:
            skipped.append({"task_id": task_id, "reason": "任务状态已变化，请刷新看板"})
            continue
        try:
            run_diagnosis_task.delay(task_id)
        except Exception:
            await update_task(
                pool,
                task_id,
                status="FAILED",
                progress=100,
                current_step="批量重试投递失败",
                error="诊断任务重新投递失败，请稍后重试",
            )
            skipped.append({"task_id": task_id, "reason": "任务队列暂不可用"})
            continue
        retried_ids.append(task_id)
        try:
            await insert_task_operation(
                pool,
                task_id,
                "BULK_RETRY",
                from_status="FAILED",
                to_status="QUEUED",
                actor=operation_actor(request),
                request_id=request_id,
                detail={"source": "project-task-board", "batch_size": len(task_ids)},
            )
        except Exception:
            continue

    details = [_operation_detail(task_id, "retried") for task_id in retried_ids]
    details.extend(
        _operation_detail(item["task_id"], "skipped", item["reason"]) for item in skipped
    )
    return {
        "project": project,
        "retried": retried_ids,
        "skipped": skipped,
        "details": details,
        "request_id": request_id,
    }
