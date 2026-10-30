"""AI 评测运行持久化和查询测试。"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.ai_evaluation_runs import insert_ai_evaluation_run, list_ai_evaluation_runs
from app.main import app


class _InsertPool:
    """捕获 AI 评测插入参数的连接池替身。"""

    def __init__(self):
        """初始化插入参数记录。"""
        self.args = None

    async def fetchrow(self, query, *args):
        """记录插入参数并返回运行 ID。

        参数:
            - `query`：AI 评测运行 INSERT SQL。
            - `args`：评测运行字段参数。
        """
        self.args = args
        return {"id": 9}


@pytest.mark.asyncio
async def test_insert_ai_evaluation_run_persists_summary() -> None:
    """验证评测汇总写入运行表和完整 JSON 结果。

    参数:
        - 无：测试直接使用固定连接池替身。
    """
    pool = _InsertPool()
    result = {
        "min_pass_rate": 1.0,
        "total": 3,
        "executed": 2,
        "skipped": 1,
        "passed": 2,
        "pass_rate": 1.0,
        "average_evidence_ref_recall": 1.0,
        "cases": [],
    }
    run_id = await insert_ai_evaluation_run(pool, "waky3", result)
    assert run_id == 9
    assert pool.args[0:2] == ("waky3", "COMPLETED")
    assert '"total": 3' in pool.args[9]


class _QueryPool:
    """返回 AI 评测运行记录的连接池替身。"""

    async def fetch(self, query, project, limit):
        """返回一条评测运行记录。

        参数:
            - `query`：AI 评测运行查询 SQL。
            - `project`：项目筛选值。
            - `limit`：返回记录数量上限。
        """
        return [
            {
                "id": 1,
                "project": project,
                "status": "COMPLETED",
                "min_pass_rate": 1.0,
                "total": 2,
                "executed": 1,
                "skipped": 1,
                "passed": 1,
                "pass_rate": 1.0,
                "average_evidence_ref_recall": 1.0,
                "error": None,
                "created_at": datetime(2026, 10, 30, tzinfo=timezone.utc),
            }
        ]


@pytest.mark.asyncio
async def test_list_ai_evaluation_runs_serializes_time() -> None:
    """验证评测运行时间转换为 ISO 字符串。

    参数:
        - 无：测试直接使用固定连接池替身。
    """
    runs = await list_ai_evaluation_runs(_QueryPool(), "waky3", 10)
    assert runs[0]["created_at"].startswith("2026-10-30T00:00:00")


def test_ai_evaluation_history_endpoint_returns_runs() -> None:
    """验证项目 AI 评测历史接口返回运行记录。

    参数:
        - 无：测试直接使用固定连接池替身。
    """
    app.state.db_pool = _QueryPool()
    try:
        response = TestClient(app).get("/api/ai/waky3/evaluations?limit=5")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["runs"][0]["passed"] == 1
