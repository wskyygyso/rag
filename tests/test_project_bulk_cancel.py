"""项目任务批量取消接口测试。"""

from fastapi.testclient import TestClient

from app import projects_api
from app.main import app


class _Pool:
    """模拟批量取消查询和更新的连接池替身。"""

    async def fetch(self, query, project, task_ids):
        """根据查询类型返回任务状态或更新结果。

        参数:
            - `query`：任务状态查询或批量更新 SQL。
            - `project`：任务所属项目。
            - `task_ids`：需要处理的任务 ID 集合。
        """
        if query.lstrip().startswith("UPDATE"):
            return [{"id": "diag_1", "status": "CANCELLED"}]
        return [
            {"id": "diag_1", "status": "RUNNING"},
            {"id": "diag_2", "status": "COMPLETED"},
        ]

    async def execute(self, query, *args):
        """模拟操作审计写入。

        参数:
            - `query`：任务操作审计 INSERT SQL。
            - `*args`：审计字段参数。
        """
        return "INSERT 0 1"


def test_project_bulk_cancel_updates_active_and_reports_skipped(monkeypatch) -> None:
    """验证批量取消只修改活动任务并返回跳过原因。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    async def fake_cancel(pool, project, task_ids):
        """返回模拟批量取消结果。

        参数:
            - `pool`：数据库连接池。
            - `project`：任务所属项目。
            - `task_ids`：需要处理的任务 ID 集合。
        """
        return [{"id": "diag_1", "status": "CANCELLED"}]

    monkeypatch.setattr(projects_api, "mark_cancelled_many", fake_cancel)
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post(
            "/api/projects/waky3/tasks/cancel",
            json={"task_ids": ["diag_1", "diag_2", "missing"]},
        )
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["cancelled"] == ["diag_1"]
    assert len(response.json()["skipped"]) == 2
