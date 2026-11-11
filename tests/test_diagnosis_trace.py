"""诊断阶段 Trace 持久化、查询和 SSE 恢复测试。"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.task_store import record_trace_span


class _TracePool:
    """为 Trace API、任务授权和 SSE 提供固定只读记录。"""

    def __init__(self) -> None:
        """初始化固定任务和阶段轨迹。"""
        self.traces = [
            {
                "id": 1,
                "task_id": "diag_trace",
                "event_type": "stage.started",
                "stage": "retrieval",
                "status": "started",
                "elapsed_ms": None,
                "payload": {"mode": "question_understanding"},
                "created_at": datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
            },
            {
                "id": 2,
                "task_id": "diag_trace",
                "event_type": "task.completed",
                "stage": "reporting",
                "status": "succeeded",
                "elapsed_ms": 18.5,
                "payload": {"confidence": "高"},
                "created_at": datetime(2026, 11, 10, 8, 0, 1, tzinfo=timezone.utc),
            },
        ]

    async def fetchrow(self, query, *args):
        """返回任务授权所需记录。

        参数:
            - `query`：任务查询 SQL。
            - `*args`：任务 ID 等查询参数。
        """
        assert "FROM diagnostic_tasks" in query
        return {
            "id": args[0],
            "project": "waky6",
            "question": "为什么业务被限制？",
            "environment": "development",
            "status": "COMPLETED",
            "progress": 100,
            "current_step": "诊断报告已生成",
            "result": {"message": "完成"},
            "error": None,
            "created_at": datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 11, 10, 8, 0, 2, tzinfo=timezone.utc),
            "request_id": "request-trace",
            "use_ai": True,
        }

    async def fetch(self, query, *args):
        """按自增 ID 返回固定轨迹。

        参数:
            - `query`：轨迹查询 SQL。
            - `*args`：任务 ID、after_id 和 limit。
        """
        assert "FROM diagnosis_trace_spans" in query
        after_id, limit = args[1], args[2]
        return [item for item in self.traces if item["id"] > after_id][:limit]


class _FailingTracePool:
    """模拟轨迹表故障，验证可观测性不能阻断主诊断。"""

    async def fetchrow(self, query, *args):
        """轨迹写入始终失败。

        参数:
            - `query`：本应执行的轨迹 INSERT。
            - `*args`：轨迹字段参数。
        """
        raise RuntimeError("trace storage unavailable")


def test_trace_api_returns_ordered_timeline_and_summary() -> None:
    """验证 Trace API 返回轨迹顺序、失败统计和最大阶段耗时。"""
    app.state.db_pool = _TracePool()
    try:
        response = TestClient(app).get("/api/diagnoses/diag_trace/trace?limit=200")
    finally:
        app.state.db_pool = None

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == [1, 2]
    assert payload["items"][0]["created_at"].startswith("2026-11-10T08:00:00")
    assert payload["summary"] == {
        "failed_count": 0,
        "degraded_count": 0,
        "max_elapsed_ms": 18.5,
    }


def test_sse_streams_trace_events_before_terminal_progress() -> None:
    """验证 SSE 可在终态前进度事件之前恢复已经写入的阶段轨迹。"""
    app.state.db_pool = _TracePool()
    try:
        response = TestClient(app).get("/api/diagnoses/diag_trace/events")
    finally:
        app.state.db_pool = None

    assert response.status_code == 200
    assert "id: trace-1\nevent: trace" in response.text
    assert "id: trace-2\nevent: trace" in response.text
    assert "event: progress" in response.text


@pytest.mark.asyncio
async def test_trace_write_failure_is_non_blocking() -> None:
    """验证轨迹数据库异常被吞掉并返回空结果，主诊断仍可继续。"""
    result = await record_trace_span(
        _FailingTracePool(),
        "diag_trace",
        "stage.failed",
        "worker",
        "failed",
        payload={"error_type": "RuntimeError"},
    )

    assert result is None
