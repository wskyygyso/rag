"""索引失败重试接口测试。"""

from fastapi.testclient import TestClient

from app.main import app
from app import index_api


class _Pool:
    """返回失败索引记录的连接池替身。"""

    async def fetchrow(self, query, run_id, project):
        """返回可重试失败记录。

        参数:
            - `query`：检索或查询文本。
            - `run_id`：运行记录 ID。
            - `project`：项目标识。
        """
        return {"id": run_id, "status": "FAILED"}


class _Queued:
    """模拟 Celery 投递结果。"""

    id = "celery-task-1"


class _Task:
    """模拟 Celery 任务入口。"""

    def delay(self, project):
        """返回排队任务标识。

        参数:
            - `project`：项目标识。
        """
        return _Queued()


def test_retry_index_run_queues_worker_task(monkeypatch) -> None:
    """验证失败索引会投递到 Worker，而不是同步阻塞 API。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    app.state.db_pool = _Pool()
    monkeypatch.setattr(index_api, "run_document_index_task", _Task(), raising=False)
    try:
        response = TestClient(app).post("/api/index/waky3/runs/7/retry")
    finally:
        app.state.db_pool = None
    assert response.status_code == 202
    assert response.json()["task_id"] == "celery-task-1"
