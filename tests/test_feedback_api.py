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

    async def fetch(self, query, task_id, limit=None):
        """返回一条按时间排序的反馈记录。"""
        if "JOIN diagnostic_tasks" in query:
            return [
                {
                    "rating": "correct",
                    "count": 2,
                    "task_count": 2,
                    "project_task_count": 2,
                    "latest_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
                },
                {
                    "rating": "escalate",
                    "count": 1,
                    "task_count": 1,
                    "project_task_count": 2,
                    "latest_at": None,
                },
            ]
        if "GROUP BY rating" in query:
            return [
                {
                    "rating": "correct",
                    "count": 2,
                    "latest_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
                },
                {"rating": "escalate", "count": 1, "latest_at": None},
            ]
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
        summary = client.get("/api/diagnoses/diag_1/feedback/summary")
        project_summary = client.get("/api/feedback/waky3/summary")
    finally:
        app.state.db_pool = None
    assert created.status_code == 201
    assert created.json()["rating"] == "correct"
    assert created.json()["created_at"].startswith("2026-09-30T00:00:00")
    assert listed.status_code == 200
    assert listed.json()["feedback"][0]["comment"] == "证据充分"
    assert summary.status_code == 200
    assert summary.json()["total"] == 3
    assert summary.json()["counts"]["correct"] == 2
    assert summary.json()["latest_at"].startswith("2026-09-30T00:00:00")
    assert project_summary.status_code == 200
    assert project_summary.json()["project"] == "waky3"
    assert project_summary.json()["task_count"] == 2
    assert project_summary.json()["total"] == 3


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
