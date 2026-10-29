"""诊断任务请求 ID 持久化测试。"""

import pytest

from app.task_store import insert_task


class _Pool:
    """捕获任务插入参数的连接池替身。"""

    def __init__(self):
        """初始化调用参数。"""
        self.args = None

    async def fetchrow(self, query, *args):
        """记录参数并返回空记录。"""
        self.args = args
        return None


@pytest.mark.asyncio
async def test_insert_task_persists_request_id() -> None:
    """验证新诊断任务保存对应 HTTP 请求 ID。"""
    pool = _Pool()
    await insert_task(
        pool,
        "diag_1",
        "waky3",
        "检查邀请",
        "development",
        request_id="request-123",
    )
    assert pool.args[-1] == "request-123"
