"""诊断任务级 AI 开关和 Worker 决策测试。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import diagnosis, worker
from app.main import app


class _Pool:
    """模拟任务写入所需的数据库连接池。"""


class _Queued:
    """模拟 Celery 投递结果。"""

    id = "celery-ai-task"


class _DiagnosisTask:
    """捕获诊断任务创建参数并模拟异步投递。"""

    def delay(self, task_id):
        """记录诊断任务已进入 Celery 队列。

        参数:
            - `task_id`：需要异步执行的诊断任务 ID。
        """
        return _Queued()


def test_create_diagnosis_passes_explicit_ai_switch(monkeypatch) -> None:
    """验证 API 将任务级 AI=false 传入任务存储。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    captured = {}

    async def fake_insert(pool, task_id, project, question, environment, created_by, validation, use_ai=None, request_id=None):
        """返回固定任务并记录 use_ai。

        参数:
            - `pool`：任务数据库连接池。
            - `task_id`：新建任务 ID。
            - `project`：诊断项目。
            - `question`：用户问题。
            - `environment`：目标环境。
            - `created_by`：任务创建者。
            - `validation`：只读验证计划。
            - `use_ai`：任务级 AI 开关。
            - `request_id`：HTTP 请求 ID。
        """
        captured["use_ai"] = use_ai
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
            "created_at": "2026-10-30T00:00:00+08:00",
            "updated_at": "2026-10-30T00:00:00+08:00",
            "use_ai": use_ai,
            "request_id": request_id,
        }

    monkeypatch.setattr(diagnosis, "insert_task", fake_insert)
    monkeypatch.setattr(worker, "run_diagnosis_task", _DiagnosisTask())
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post(
            "/api/diagnoses",
            json={"project": "waky3", "question": "检查邀请", "use_ai": False},
        )
    finally:
        app.state.db_pool = None
    assert response.status_code == 202
    assert captured["use_ai"] is False
    assert response.json()["use_ai"] is False


def test_create_diagnosis_rejects_ai_when_global_llm_disabled(monkeypatch) -> None:
    """验证全局 LLM 未启用时不能显式请求 AI 叙写。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    monkeypatch.setattr(diagnosis.settings, "llm_enabled", False)
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).post(
            "/api/diagnoses",
            json={"project": "waky3", "question": "检查邀请", "use_ai": True},
        )
    finally:
        app.state.db_pool = None
    assert response.status_code == 400
    assert response.json()["detail"] == "LLM 诊断尚未启用"


@pytest.mark.parametrize(
    ("llm_enabled", "use_ai", "evidence", "expected"),
    [
        (True, None, 2, True),
        (True, False, 2, False),
        (False, True, 2, False),
        (True, True, 0, False),
    ],
)
def test_worker_ai_decision_respects_global_task_and_evidence(
    llm_enabled, use_ai, evidence, expected
) -> None:
    """验证全局开关、任务开关和证据门槛共同决定 AI 调用。

    参数:
        - `llm_enabled`：全局 LLM 是否启用。
        - `use_ai`：任务级 AI 开关。
        - `evidence`：当前任务可用证据数。
        - `expected`：期望是否调用 LLM。
    """
    settings = SimpleNamespace(llm_enabled=llm_enabled, llm_min_evidence_items=1)
    assert worker.should_run_ai(settings, {"use_ai": use_ai}, evidence) is expected
