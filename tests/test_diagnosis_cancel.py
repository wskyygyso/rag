"""诊断任务取消接口测试。"""

from fastapi.testclient import TestClient

from app.main import app


class _Pool:
    """模拟任务取消与任务查询的连接池。"""

    def __init__(self, status: str = "RUNNING"):
        """保存任务当前状态。

        参数:
            - `status`：状态值，类型为 `str`。
        """
        self.status = status

    async def fetchrow(self, query, task_id):
        """根据更新或查询 SQL 返回任务记录。

        参数:
            - `query`：检索或查询文本。
            - `task_id`：诊断任务 ID。
        """
        if task_id == "missing":
            return None
        if query.lstrip().startswith("UPDATE"):
            if self.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                return None
            self.status = "CANCELLED"
        return {
            "id": task_id,
            "project": "waky3",
            "question": "检查邀请失败",
            "environment": "development",
            "status": self.status,
            "progress": 100,
            "current_step": "任务已取消",
            "result": None,
            "error": None,
            "created_at": "2026-10-08T00:00:00+08:00",
            "updated_at": "2026-10-08T00:00:00+08:00",
        }

    async def fetch(self, query, task_id, limit):
        """返回一条任务操作审计记录。

        参数:
            - `query`：检索或查询文本。
            - `task_id`：诊断任务 ID。
            - `limit`：单次结果数量上限。
        """
        return [{"id": 1, "task_id": task_id, "operation": "CANCEL", "from_status": "RUNNING", "to_status": "CANCELLED", "actor": "reviewer", "detail": {}, "created_at": "2026-10-08T00:00:00+08:00"}]


def test_running_diagnosis_can_be_cancelled() -> None:
    """验证执行中的任务可以取消并返回终态摘要。"""
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post("/api/diagnoses/diag_1/cancel")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["progress"] == 100


def test_cancel_rejects_missing_or_terminal_task() -> None:
    """验证不存在任务和已结束任务不能取消。"""
    client = TestClient(app)
    app.state.db_pool = _Pool(status="COMPLETED")
    try:
        terminal = client.post("/api/diagnoses/diag_1/cancel")
        missing = client.post("/api/diagnoses/missing/cancel")
    finally:
        app.state.db_pool = None
    assert terminal.status_code == 409
    assert missing.status_code == 404


def test_task_operations_returns_audit_trail() -> None:
    """验证任务操作审计轨迹可以查询。"""
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).get("/api/diagnoses/diag_1/operations?limit=10")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["operations"][0]["operation"] == "CANCEL"
