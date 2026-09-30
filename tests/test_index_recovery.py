"""索引运行失败恢复测试。"""

import pytest

from app.task_store import recover_stale_document_index_runs


class _Pool:
    """捕获索引恢复 SQL 的连接池替身。"""

    def __init__(self, result: str = "UPDATE 2") -> None:
        self.result = result
        self.calls = []

    async def execute(self, query: str, *args: object) -> str:
        """返回模拟更新数量。"""
        self.calls.append((query, args))
        return self.result


@pytest.mark.asyncio
async def test_recover_stale_index_runs_marks_timeout() -> None:
    """验证超时索引被标记失败并返回恢复数量。"""
    pool = _Pool()
    assert await recover_stale_document_index_runs(pool, "waky3", 30) == 2
    assert pool.calls[0][1] == ("waky3", 30)


@pytest.mark.asyncio
async def test_cleanup_index_runs_returns_deleted_count() -> None:
    """验证索引清理返回删除数量。"""
    pool = _Pool("DELETE 3")
    from app.task_store import cleanup_document_index_runs

    assert await cleanup_document_index_runs(pool, "waky3", 20) == 3
    assert pool.calls[0][1] == ("waky3", 20)


def test_recover_stale_index_runs_rejects_invalid_window() -> None:
    """验证索引恢复窗口存在上下界。"""
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(recover_stale_document_index_runs(_Pool(), "waky3", 0))
