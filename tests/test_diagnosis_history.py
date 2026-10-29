"""历史诊断任务浏览接口测试。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app


class _Pool:
    """返回固定历史任务列表的连接池替身。"""

    async def fetch(self, query, *args):
        """返回带时间字段的历史任务摘要。"""
        assert "ORDER BY created_at DESC" in query
        assert "request_id = $" in query
        assert args[-2:] == (0, 10)
        return [
            {
                "id": "diag_old",
                "project": "waky3",
                "question": "检查邀请失败",
                "environment": "development",
                "status": "COMPLETED",
                "progress": 100,
                "current_step": "完成",
                "error": None,
                "created_at": datetime(2026, 10, 8, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 10, 8, tzinfo=timezone.utc),
            }
        ]


def test_history_endpoint_filters_and_serializes_tasks() -> None:
    """验证历史任务筛选参数和时间字段序列化。"""
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).get(
            "/api/diagnoses/history?project=waky3&status=COMPLETED&query=邀请&request_id=request-1&days=30&limit=10"
        )
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == "diag_old"
    assert payload["items"][0]["created_at"].startswith("2026-10-08T00:00:00")
    assert payload["limit"] == 10


def test_history_endpoint_rejects_invalid_filters() -> None:
    """验证历史任务状态、时间范围和分页上限受到约束。"""
    app.state.db_pool = _Pool()
    try:
        client = TestClient(app)
        invalid_status = client.get("/api/diagnoses/history?status=completed")
        invalid_days = client.get("/api/diagnoses/history?days=0")
        invalid_limit = client.get("/api/diagnoses/history?limit=101")
    finally:
        app.state.db_pool = None
    assert invalid_status.status_code == 422
    assert invalid_days.status_code == 422
    assert invalid_limit.status_code == 422
