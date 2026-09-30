"""诊断报告人工反馈接口测试。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app


class _Pool:
    """模拟任务和反馈数据库操作的连接池。"""

    async def fetchrow(self, query, *args):
        """根据 SQL 操作类型返回任务或反馈记录。"""
        if "SELECT * FROM diagnostic_tasks" in query:
            if args[0] == "missing":
                return None
            return {"id": args[0], "status": "COMPLETED"}
        return {
            "id": 8,
            "task_id": args[0],
            "rating": args[1],
            "comment": args[2],
            "submitted_by": args[3],
            "created_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
        }

    async def fetch(self, query, task_id, limit):
        """返回一条按时间排序的反馈记录。"""
        return [
            {
                "id": 8,
                "task_id": task_id,
                "rating": "correct",
                "comment": "证据充分",
                "submitted_by": "reviewer",
                "created_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
            }
        ]


def test_feedback_endpoint_creates_and_lists_feedback() -> None:
    """验证反馈创建、查询和时间字段序列化。"""
    app.state.db_pool = _Pool()
    try:
        client = TestClient(app)
        created = client.post(
            "/api/diagnoses/diag_1/feedback",
            json={
                "rating": "correct",
                "comment": "证据充分",
                "submitted_by": "reviewer",
            },
        )
        listed = client.get("/api/diagnoses/diag_1/feedback?limit=10")
    finally:
        app.state.db_pool = None
    assert created.status_code == 201
    assert created.json()["rating"] == "correct"
    assert created.json()["created_at"].startswith("2026-09-30T00:00:00")
    assert listed.status_code == 200
    assert listed.json()["feedback"][0]["comment"] == "证据充分"


def test_feedback_endpoint_validates_task_and_rating() -> None:
    """验证不存在任务和非法反馈类型返回受控错误。"""
    app.state.db_pool = _Pool()
    try:
        client = TestClient(app)
        missing = client.post(
            "/api/diagnoses/missing/feedback", json={"rating": "correct"}
        )
        invalid = client.post(
            "/api/diagnoses/diag_1/feedback", json={"rating": "unknown"}
        )
    finally:
        app.state.db_pool = None
    assert missing.status_code == 404
    assert invalid.status_code == 422
