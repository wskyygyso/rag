"""项目发现和项目概览测试。"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import projects, projects_api
from app.auth import ApiIdentity
from app.main import app
from app.projects import (
    discover_projects,
    get_latest_project_index_runs,
    get_project_overview,
    get_project_task_board,
)


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


class _ProjectListPool:
    """返回项目列表批量索引状态的内存连接池替身。"""

    async def fetch(self, query, project_names):
        """返回一个项目的最新索引运行。

        参数:
            - `query`：按项目批量读取最新索引运行的 SQL。
            - `project_names`：调用方有权访问的项目名列表。
        """
        assert project_names == ["waky3", "waky4"]
        return [
            {
                "project": "waky3",
                "status": "COMPLETED",
                "chunk_count": 321,
                "repository_revision": "abc123",
                "index_version": "v1",
                "error": None,
                "started_at": datetime(2026, 11, 6, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 11, 6, 0, 1, tzinfo=timezone.utc),
            }
        ]


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


@pytest.mark.asyncio
async def test_latest_project_indexes_are_returned_by_project_name() -> None:
    """验证项目列表可一次读取多个项目的最新索引状态。"""
    latest = await get_latest_project_index_runs(_ProjectListPool(), ["waky3", "waky4"])
    assert latest["waky3"]["chunk_count"] == 321
    assert latest["waky3"]["finished_at"] == "2026-11-06T00:01:00+00:00"
    assert "waky4" not in latest


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
    (tmp_path / "waky4").mkdir()
    monkeypatch.setattr(projects_api, "settings", type("Settings", (), {"code_repositories_root": str(tmp_path)})())
    app.state.db_pool = _ProjectListPool()
    try:
        response = TestClient(app).get("/api/projects")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    projects = {item["name"]: item for item in response.json()["projects"]}
    assert set(projects) == {"waky3", "waky4"}
    assert projects["waky3"]["index_status"] == "COMPLETED"
    assert projects["waky3"]["indexed_chunk_count"] == 321
    assert projects["waky4"]["index_status"] == "NOT_INDEXED"
    assert projects["waky4"]["indexed_chunk_count"] == 0


class _BoardPool:
    """返回项目任务看板聚合数据的连接池替身。"""

    async def fetch(self, query, project, *args):
        """按任务状态查询返回不同集合。

        参数:
            - `query`：任务看板查询 SQL。
            - `project`：项目筛选值。
            - `*args`：最近任务数量等附加参数。
        """
        if "GROUP BY status" in query:
            return [{"status": "RUNNING", "count": 1}, {"status": "COMPLETED", "count": 2}]
        if "status IN ('QUEUED'" in query:
            return [
                {
                    "id": "diag_active",
                    "question": "运行中问题",
                    "status": "RUNNING",
                    "progress": 50,
                    "current_step": "检索",
                    "created_at": "2026-11-02T00:00:00+08:00",
                    "updated_at": "2026-11-02T00:01:00+08:00",
                }
            ]
        return [
            {
                "id": "diag_done",
                "question": "已完问题",
                "status": "COMPLETED",
                "progress": 100,
                "current_step": "完成",
                "created_at": "2026-11-02T00:00:00+08:00",
                "updated_at": "2026-11-02T00:02:00+08:00",
            }
        ]


@pytest.mark.asyncio
async def test_project_task_board_aggregates_active_and_recent_tasks() -> None:
    """验证任务看板聚合状态、运行中任务和最近任务。

    参数:
        - 无：测试使用固定连接池替身。
    """
    board = await get_project_task_board(_BoardPool(), "waky3", 5)
    assert board["status_counts"]["RUNNING"] == 1
    assert board["active_tasks"][0]["id"] == "diag_active"
    assert board["recent_tasks"][0]["id"] == "diag_done"


def test_project_task_board_endpoint_returns_board() -> None:
    """验证项目任务看板 API 返回运行中和最近任务。

    参数:
        - 无：测试使用固定连接池替身。
    """
    app.state.db_pool = _BoardPool()
    try:
        response = TestClient(app).get("/api/projects/waky3/task-board")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["project"] == "waky3"
