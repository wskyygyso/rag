"""历史诊断任务重新运行接口测试。"""

from fastapi.testclient import TestClient

from app import diagnosis, worker
from app.main import app


class _Pool:
    """模拟重新诊断所需的数据库连接池。"""


class _Queued:
    """模拟 Celery 任务投递结果。"""

    id = "celery-rerun"


class _Task:
    """模拟诊断 Celery 入口。"""

    def __init__(self):
        """初始化任务投递记录。"""
        self.task_ids = []

    def delay(self, task_id):
        """记录新诊断任务 ID。

        参数:
            - `task_id`：重新诊断创建的新任务 ID。
        """
        self.task_ids.append(task_id)
        return _Queued()


def test_rerun_creates_new_task_and_copies_configuration(monkeypatch) -> None:
    """验证重新诊断复制配置但创建新任务 ID。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    source = {
        "id": "diag_old",
        "project": "waky3",
        "question": "检查邀请",
        "environment": "production-readonly",
        "created_by": "reviewer",
        "validation_plan": {"database": {"sql": "SELECT 1"}},
        "use_ai": False,
        "status": "COMPLETED",
    }
    captured = {}

    async def fake_authorized(pool, request, task_id):
        """返回历史任务。

        参数:
            - `pool`：数据库连接池。
            - `request`：FastAPI 请求上下文。
            - `task_id`：历史任务 ID。
        """
        return source

    async def fake_insert(pool, task_id, project, question, environment, created_by, validation, use_ai=None, request_id=None):
        """返回新建任务并记录复制配置。

        参数:
            - `pool`：数据库连接池。
            - `task_id`：新建任务 ID。
            - `project`：诊断项目。
            - `question`：诊断问题。
            - `environment`：目标环境。
            - `created_by`：任务创建者。
            - `validation`：复制到新任务的验证计划。
            - `use_ai`：复制到新任务的 AI 开关。
            - `request_id`：HTTP 请求 ID。
        """
        captured.update(
            task_id=task_id,
            project=project,
            question=question,
            environment=environment,
            validation=validation,
            use_ai=use_ai,
        )
        return {
            "id": task_id,
            "project": project,
            "question": question,
            "environment": environment,
            "status": "QUEUED",
            "progress": 0,
            "current_step": "等待",
            "result": None,
            "error": None,
            "created_at": "2026-11-02T00:00:00+08:00",
            "updated_at": "2026-11-02T00:00:00+08:00",
            "use_ai": use_ai,
            "request_id": request_id,
        }

    async def fake_record(*args, **kwargs):
        """忽略操作审计写入。

        参数:
            - `*args`：操作审计的位置参数。
            - `**kwargs`：操作审计的关键字参数。
        """
        return None

    queue = _Task()
    monkeypatch.setattr(diagnosis, "_authorized_task", fake_authorized)
    monkeypatch.setattr(diagnosis, "insert_task", fake_insert)
    monkeypatch.setattr(diagnosis, "_record_operation", fake_record)
    monkeypatch.setattr(worker, "run_diagnosis_task", queue)
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post("/api/diagnoses/diag_old/rerun")
    finally:
        app.state.db_pool = None
    assert response.status_code == 202
    assert captured["task_id"] != "diag_old"
    assert captured["project"] == "waky3"
    assert captured["validation"] == {"database": {"sql": "SELECT 1"}}
    assert captured["use_ai"] is False
    assert queue.task_ids == [captured["task_id"]]
