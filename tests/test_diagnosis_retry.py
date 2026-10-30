"""失败诊断任务人工重试接口测试。"""

from fastapi.testclient import TestClient

from app import worker
from app.main import app


class _Pool:
    """模拟失败任务重置和任务查询的连接池。"""

    def __init__(self, status: str = "FAILED", retry_count: int = 0):
        """保存待模拟的任务状态和重试次数。

        参数:
            - `status`：状态值，类型为 `str`。
            - `retry_count`：任务已人工重试次数，类型为 `int`。
        """
        self.status = status
        self.retry_count = retry_count

    async def fetchrow(self, query, task_id, *args):
        """按更新或查询 SQL 返回对应任务记录。

        参数:
            - `query`：检索或查询文本。
            - `task_id`：诊断任务 ID。
            - `*args`：可变位置参数集合。
        """
        if "retry_count = retry_count + 1" in query:
            if self.status != "FAILED" or self.retry_count >= 3:
                return None
            self.status = "QUEUED"
            self.retry_count += 1
        elif query.lstrip().startswith("UPDATE"):
            self.status = args[0] or self.status
        if task_id == "missing":
            return None
        return {
            "id": task_id,
            "project": "waky3",
            "question": "检查邀请失败",
            "environment": "development",
            "status": self.status,
            "progress": 0,
            "current_step": "等待 Worker 重新执行",
            "result": None,
            "error": None,
            "retry_count": self.retry_count,
            "created_at": "2026-10-08T00:00:00+08:00",
            "updated_at": "2026-10-08T00:00:00+08:00",
        }


class _QueuedTask:
    """记录重新投递任务 ID 的 Celery 入口替身。"""

    def __init__(self):
        """初始化投递记录。"""
        self.task_ids = []

    def delay(self, task_id):
        """记录任务 ID，模拟异步队列投递。

        参数:
            - `task_id`：诊断任务 ID。
        """
        self.task_ids.append(task_id)


class _BrokenTask:
    """模拟无法连接队列的 Celery 任务入口。"""

    def delay(self, task_id):
        """抛出投递异常，验证任务状态恢复逻辑。

        参数:
            - `task_id`：诊断任务 ID。
        """
        raise RuntimeError("broker unavailable")


def test_failed_diagnosis_can_be_retried(monkeypatch) -> None:
    """验证失败任务会重置并重新投递到 Worker。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    queued = _QueuedTask()
    monkeypatch.setattr(worker, "run_diagnosis_task", queued)
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post("/api/diagnoses/diag_1/retry")
    finally:
        app.state.db_pool = None
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert queued.task_ids == ["diag_1"]


def test_retry_rejects_non_failed_or_exhausted_task() -> None:
    """验证非失败任务和达到上限的任务不能人工重试。"""
    client = TestClient(app)
    app.state.db_pool = _Pool(status="COMPLETED")
    try:
        completed = client.post("/api/diagnoses/diag_1/retry")
        app.state.db_pool = _Pool(status="FAILED", retry_count=3)
        exhausted = client.post("/api/diagnoses/diag_1/retry")
    finally:
        app.state.db_pool = None
    assert completed.status_code == 409
    assert exhausted.status_code == 409
    assert exhausted.json()["detail"] == "任务已达到人工重试上限"


def test_retry_marks_task_failed_when_queue_is_unavailable(monkeypatch) -> None:
    """验证队列投递失败时接口返回 503，避免任务停留在排队状态。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    pool = _Pool()
    monkeypatch.setattr(worker, "run_diagnosis_task", _BrokenTask())
    app.state.db_pool = pool
    try:
        response = TestClient(app).post("/api/diagnoses/diag_1/retry")
    finally:
        app.state.db_pool = None
    assert response.status_code == 503
    assert response.json()["detail"] == "任务重新投递失败"
    assert pool.status == "FAILED"
