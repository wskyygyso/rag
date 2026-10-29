"""诊断任务操作审计持久化测试。"""

import pytest

from app.task_store import insert_task_operation


class _Pool:
    """记录任务操作审计参数的连接池替身。"""

    def __init__(self):
        """初始化调用记录。"""
        self.calls = []

    async def execute(self, query, *args):
        """保存 SQL 和参数，模拟异步写入。"""
        self.calls.append((query, args))


@pytest.mark.asyncio
async def test_insert_task_operation_writes_status_and_actor() -> None:
    """验证操作审计保存状态变化、操作方和结构化摘要。"""
    pool = _Pool()
    await insert_task_operation(
        pool,
        "diag_1",
        "RETRY",
        from_status="FAILED",
        to_status="QUEUED",
        actor="reviewer",
        request_id="request-1",
        detail={"source": "ui"},
    )
    assert len(pool.calls) == 1
    assert pool.calls[0][1][:6] == (
        "diag_1",
        "RETRY",
        "FAILED",
        "QUEUED",
        "reviewer",
        "request-1",
    )
    assert '"source": "ui"' in pool.calls[0][1][6]
