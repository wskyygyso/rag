"""项目发现和项目概览测试。"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import projects, projects_api
from app.auth import ApiIdentity
from app.main import app
from app.projects import discover_projects, get_project_overview


def test_discover_projects_respects_directory_and_identity(tmp_path) -> None:
    """验证项目发现只返回目录白名单内且有权访问的项目。

    参数:
        - `tmp_path`：pytest 临时仓库根目录。
    """
    (tmp_path / "waky3").mkdir()
    (tmp_path / "waky4").mkdir()
    (tmp_path / ".hidden").mkdir()
    identity = ApiIdentity("reader", frozenset({"waky3"}), frozenset({"read"}))
    result = discover_projects(str(tmp_path), identity)
    assert [item["name"] for item in result] == ["waky3"]


class _Pool:
    """返回项目任务、索引和反馈聚合结果的连接池替身。"""

    async def fetch(self, query, project):
        """按查询类型返回任务状态或反馈分布。

        参数:
            - `query`：项目概览聚合 SQL。
            - `project`：项目筛选值。
        """
        if "diagnostic_feedback" in query:
            return [{"rating": "correct", "count": 2}]
        return [{"status": "COMPLETED", "count": 3}, {"status": "FAILED", "count": 1}]

    async def fetchrow(self, query, project):
        """返回最近索引运行。

        参数:
            - `query`：最近索引运行查询 SQL。
            - `project`：项目筛选值。
        """
        return {
            "id": 7,
            "status": "COMPLETED",
            "chunk_count": 20,
            "repository_revision": "abc123",
            "index_version": "v1",
            "error": None,
            "started_at": datetime(2026, 11, 2, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 11, 2, 0, 1, tzinfo=timezone.utc),
        }


@pytest.mark.asyncio
async def test_project_overview_aggregates_status_feedback_and_index() -> None:
    """验证项目概览聚合任务、反馈和索引状态。

    参数:
        - 无：测试使用固定连接池替身。
    """
    overview = await get_project_overview(_Pool(), "waky3")
    assert overview["tasks_total"] == 4
    assert overview["feedback"]["correct"] == 2
    assert overview["latest_index_run"]["chunk_count"] == 20


def test_project_overview_endpoint_returns_accessible_project(monkeypatch, tmp_path) -> None:
    """验证项目概览 API 返回项目版本和数据库聚合。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
        - `tmp_path`：pytest 临时仓库根目录。
    """
    (tmp_path / "waky3").mkdir()
    monkeypatch.setattr(projects_api, "settings", type("Settings", (), {"code_repositories_root": str(tmp_path)})())
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).get("/api/projects/waky3/overview")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["project"] == "waky3"


def test_project_list_endpoint_returns_discovered_projects(monkeypatch, tmp_path) -> None:
    """验证项目列表 API 返回白名单目录。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
        - `tmp_path`：pytest 临时仓库根目录。
    """
    (tmp_path / "waky3").mkdir()
    monkeypatch.setattr(projects_api, "settings", type("Settings", (), {"code_repositories_root": str(tmp_path)})())
    response = TestClient(app).get("/api/projects")
    assert response.status_code == 200
    assert response.json()["projects"][0]["name"] == "waky3"
