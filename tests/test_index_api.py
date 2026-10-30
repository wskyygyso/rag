"""文档索引运行状态接口测试。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app


class _Pool:
    """返回索引运行记录的连接池替身。"""

    async def fetch(self, query, project, limit):
        """返回一条带版本信息的索引记录。

        参数:
            - `query`：检索或查询文本。
            - `project`：项目标识。
            - `limit`：单次结果数量上限。
        """
        return [
            {
                "id": 1,
                "project": project,
                "status": "COMPLETED",
                "chunk_count": 3,
                "repository_revision": "abc123",
                "index_version": "v1",
                "error": None,
                "started_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 9, 30, 0, 1, tzinfo=timezone.utc),
            }
        ]


def test_index_runs_returns_revision_and_status() -> None:
    """验证索引状态接口返回仓库版本、索引版本和时间字段。"""
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).get("/api/index/waky3/runs?limit=5")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["runs"][0]["repository_revision"] == "abc123"
    assert response.json()["runs"][0]["index_version"] == "v1"
