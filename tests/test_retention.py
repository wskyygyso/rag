"""历史数据保留策略和分批清理测试。"""

import pytest

from app.retention import cleanup_retention


class _Pool:
    """模拟保留期限计数和删除操作的连接池。"""

    def __init__(self, execute_value="DELETE 0"):
        """保存删除结果和调用记录。"""
        self.execute_value = execute_value
        self.execute_calls = []

    async def fetchval(self, query, days):
        """为每个数据目标返回固定候选数。"""
        return 5

    async def execute(self, query, days, batch_size):
        """记录删除批次并返回预设受影响行数。"""
        self.execute_calls.append((query, days, batch_size))
        return self.execute_value


@pytest.mark.asyncio
async def test_cleanup_retention_dry_run_never_deletes() -> None:
    """验证默认试运行只统计候选数据。"""
    pool = _Pool()
    result = await cleanup_retention(
        pool,
        {
            "tasks": 90,
            "validation_audit": 180,
            "security_audit": 180,
            "document_index_runs": 90,
        },
        apply=False,
    )
    assert result["mode"] == "dry-run"
    assert result["candidate_counts"]["tasks"] == 5
    assert all(value == 0 for value in result["deleted_counts"].values())
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_cleanup_retention_apply_uses_batches() -> None:
    """验证应用模式按批删除，并在不足一批时结束。"""
    pool = _Pool(execute_value="DELETE 2")
    result = await cleanup_retention(
        pool,
        {
            "tasks": 90,
            "validation_audit": 180,
            "security_audit": 180,
            "document_index_runs": 90,
        },
        batch_size=2,
        max_batches=3,
        apply=True,
    )
    assert result["mode"] == "apply"
    assert result["deleted_counts"]["tasks"] == 6
    assert len(pool.execute_calls) == len(result["deleted_counts"]) * 3
    assert result["complete"] is False


@pytest.mark.asyncio
async def test_cleanup_retention_rejects_invalid_policy() -> None:
    """验证缺少目标或非法期限时拒绝执行。"""
    with pytest.raises(ValueError):
        await cleanup_retention(_Pool(), {}, apply=False)
