"""诊断应用流水线生命周期测试。"""

from datetime import datetime, timezone

import pytest

from app.application.diagnosis.context import DiagnosisContext
from app.application.diagnosis.pipeline import DiagnosisPipeline


def test_context_budget_and_remaining_time_are_deterministic() -> None:
    """验证诊断上下文按固定开始时间计算剩余预算。"""
    started = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    context = DiagnosisContext(task_id="diag_contract", started_at=started)
    context.bind_timeout(90)

    remaining = context.remaining_seconds(datetime(2026, 11, 10, 8, 0, 30, tzinfo=timezone.utc))

    assert remaining == 60


@pytest.mark.asyncio
async def test_pipeline_emits_lifecycle_events_and_uses_injected_executor() -> None:
    """验证流水线不依赖 Celery 即可执行并产生起止事件。"""
    calls = []
    events = []

    async def executor(task_id: str) -> None:
        """记录被调用的诊断任务 ID。

        参数:
            - `task_id`：流水线传入的任务 ID。
        """
        calls.append(task_id)

    pipeline = DiagnosisPipeline(executor=executor, event_sink=events.append)

    context = await pipeline.run("diag_pipeline")

    assert calls == ["diag_pipeline"]
    assert context.current_stage == "completed"
    assert [event.event_type.value for event in events] == ["task.started", "task.completed"]


@pytest.mark.asyncio
async def test_pipeline_preserves_executor_exception_and_emits_failure() -> None:
    """验证执行器异常会写入失败事件并原样交给 Worker。"""
    events = []

    async def executor(task_id: str) -> None:
        """模拟诊断执行失败。

        参数:
            - `task_id`：流水线传入的任务 ID。
        """
        raise RuntimeError(f"failed:{task_id}")

    pipeline = DiagnosisPipeline(executor=executor, event_sink=events.append)

    with pytest.raises(RuntimeError, match="failed:diag_failure"):
        await pipeline.run("diag_failure")

    assert events[-1].as_dict()["payload"] == {"error_type": "RuntimeError"}
