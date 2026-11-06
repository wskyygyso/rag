"""项目任务看板批量重试接口测试。"""

from fastapi.testclient import TestClient

from app import worker
from app.main import app


def _task(task_id: str, status: str, retry_count: int = 0) -> dict:
    """构造批量重试测试所需的任务数据库记录。

    参数:
        - `task_id`：诊断任务 ID。
        - `status`：任务当前状态。
        - `retry_count`：任务已人工重试次数。
    """
    return {
        "id": task_id,
        "project": "waky3",
        "question": f"问题 {task_id}",
        "environment": "development",
        "status": status,
        "progress": 100 if status == "FAILED" else 0,
        "current_step": "等待 Worker 重新执行" if status != "FAILED" else "任务执行失败",
        "result": None,
        "error": "原失败原因" if status == "FAILED" else None,
        "retry_count": retry_count,
        "created_at": "2026-11-06T00:00:00+08:00",
        "updated_at": "2026-11-06T00:00:00+08:00",
    }


class _Pool:
    """模拟项目任务查询、状态重置和操作审计写入的连接池。"""

    def __init__(self) -> None:
        """初始化包含可重试、已完成和达到上限任务的测试数据。"""
        self.tasks = {
            "diag_retry": _task("diag_retry", "FAILED", 1),
            "diag_done": _task("diag_done", "COMPLETED"),
            "diag_exhausted": _task("diag_exhausted", "FAILED", 3),
        }
        self.audits = []

    async def fetch(self, query, project, task_ids):
        """返回当前项目内被选中的任务状态记录。

        参数:
            - `query`：项目任务状态查询 SQL。
            - `project`：任务所属项目。
            - `task_ids`：调用方请求重试的任务 ID。
        """
        return [dict(self.tasks[task_id]) for task_id in task_ids if task_id in self.tasks]

    async def fetchrow(self, query, task_id, *args):
        """模拟失败任务原子重置或队列失败后的状态回写。

        参数:
            - `query`：任务更新 SQL。
            - `task_id`：需要更新的诊断任务 ID。
            - `*args`：状态更新 SQL 的附加参数。
        """
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if "retry_count = retry_count + 1" in query:
            if task["status"] != "FAILED" or task["retry_count"] >= int(args[0]):
                return None
            task.update(
                status="QUEUED",
                progress=0,
                current_step="等待 Worker 重新执行",
                result=None,
                error=None,
                retry_count=task["retry_count"] + 1,
            )
        elif query.lstrip().startswith("UPDATE"):
            task.update(
                status=args[0] or task["status"],
                progress=args[1] if args[1] is not None else task["progress"],
                current_step=args[2] or task["current_step"],
                error=args[4] or task["error"],
            )
        return dict(task)

    async def execute(self, query, *args):
        """记录成功重试后的任务操作审计。

        参数:
            - `query`：任务操作审计写入 SQL。
            - `*args`：任务 ID、操作类型、状态和操作人字段。
        """
        self.audits.append((query, args))
        return "INSERT 0 1"


class _Queue:
    """记录批量重试成功投递任务 ID 的 Celery 入口替身。"""

    def __init__(self) -> None:
        """初始化任务投递记录。"""
        self.task_ids = []

    def delay(self, task_id: str) -> None:
        """记录队列投递，模拟 Celery 成功接收任务。

        参数:
            - `task_id`：被重新投递的诊断任务 ID。
        """
        self.task_ids.append(task_id)


class _BrokenQueue:
    """模拟不可用的 Celery broker。"""

    def delay(self, task_id: str) -> None:
        """抛出投递异常以验证单任务失败回滚。

        参数:
            - `task_id`：尝试重新投递的诊断任务 ID。
        """
        raise RuntimeError("broker unavailable")


def test_project_bulk_retry_only_requeues_eligible_failed_tasks(monkeypatch) -> None:
    """验证批量重试过滤非失败、达到上限和跨项目任务并记录逐项结果。

    参数:
        - `monkeypatch`：pytest 运行期替换 Worker 入口的工具。
    """
    pool = _Pool()
    queue = _Queue()
    monkeypatch.setattr(worker, "run_diagnosis_task", queue)
    app.state.db_pool = pool
    try:
        response = TestClient(app).post(
            "/api/projects/waky3/tasks/retry",
            json={
                "task_ids": [
                    "diag_retry",
                    "diag_done",
                    "diag_exhausted",
                    "missing",
                    "diag_retry",
                ]
            },
        )
    finally:
        app.state.db_pool = None
    body = response.json()
    assert response.status_code == 202
    assert body["retried"] == ["diag_retry"]
    assert [item["task_id"] for item in body["skipped"]] == [
        "diag_done",
        "diag_exhausted",
        "missing",
    ]
    assert len(body["details"]) == 4
    assert pool.tasks["diag_retry"]["status"] == "QUEUED"
    assert queue.task_ids == ["diag_retry"]
    assert len(pool.audits) == 1


def test_project_bulk_retry_reports_broker_failure_without_leaking_details(monkeypatch) -> None:
    """验证队列不可用时不泄露内部异常文本，并恢复失败任务状态。

    参数:
        - `monkeypatch`：pytest 运行期替换 Worker 入口的工具。
    """
    pool = _Pool()
    monkeypatch.setattr(worker, "run_diagnosis_task", _BrokenQueue())
    app.state.db_pool = pool
    try:
        response = TestClient(app).post(
            "/api/projects/waky3/tasks/retry",
            json={"task_ids": ["diag_retry"]},
        )
    finally:
        app.state.db_pool = None
    body = response.json()
    assert response.status_code == 202
    assert body["retried"] == []
    assert body["skipped"] == [{"task_id": "diag_retry", "reason": "任务队列暂不可用"}]
    assert pool.tasks["diag_retry"]["status"] == "FAILED"
    assert "broker unavailable" not in response.text


def test_project_bulk_retry_rejects_more_than_twenty_tasks() -> None:
    """验证批量重试接口在进入数据库前执行数量上限校验。"""
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post(
            "/api/projects/waky3/tasks/retry",
            json={"task_ids": [f"diag_{index}" for index in range(21)]},
        )
    finally:
        app.state.db_pool = None
    assert response.status_code == 422
