"""项目级文档索引重建接口测试。"""

from fastapi.testclient import TestClient

from app import projects_api
from app.main import app


class _Pool:
    """模拟索引运行状态查询的连接池替身。"""

    def __init__(self, running=None):
        """保存当前运行记录。

        参数:
            - `running`：最近 RUNNING 索引记录；None 表示没有运行任务。
        """
        self.running = running

    async def fetchrow(self, query, project):
        """返回预设运行状态。

        参数:
            - `query`：索引运行状态查询 SQL。
            - `project`：项目筛选值。
        """
        return self.running


class _Queued:
    """模拟 Celery 任务投递结果。"""

    id = "index-task-1"


class _IndexTask:
    """捕获索引重建投递。"""

    def __init__(self):
        """初始化项目投递记录。"""
        self.projects = []

    def delay(self, project):
        """记录项目并返回排队任务。

        参数:
            - `project`：需要重建文档索引的项目标识。
        """
        self.projects.append(project)
        return _Queued()


def test_project_index_rebuild_queues_worker(monkeypatch) -> None:
    """验证项目索引重建投递到独立 Worker。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    task = _IndexTask()
    monkeypatch.setattr(projects_api, "run_document_index_task", task, raising=False)
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post("/api/projects/waky3/index")
    finally:
        app.state.db_pool = None
    assert response.status_code == 202
    assert response.json()["task_id"] == "index-task-1"
    assert task.projects == ["waky3"]


def test_project_index_rebuild_rejects_running_job(monkeypatch) -> None:
    """验证运行中的索引不能被重复投递。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    task = _IndexTask()
    monkeypatch.setattr(projects_api, "run_document_index_task", task, raising=False)
    app.state.db_pool = _Pool(running={"id": 7})
    try:
        response = TestClient(app).post("/api/projects/waky3/index")
    finally:
        app.state.db_pool = None
    assert response.status_code == 409
    assert task.projects == []
