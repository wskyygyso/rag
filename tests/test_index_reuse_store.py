"""可复用文档索引查询测试。"""

import pytest

from app.task_store import find_reusable_document_index_run


class _Pool:
    """捕获索引复用查询的连接池替身。"""

    def __init__(self):
        """初始化 SQL 和参数记录。"""
        self.query = None
        self.args = None

    async def fetchrow(self, query, *args):
        """返回一条可复用记录。"""
        self.query = query
        self.args = args
        return {"id": 9, "chunk_count": 20, "actual_chunks": 20}


@pytest.mark.asyncio
async def test_reusable_index_query_requires_revision_version_and_complete_chunks() -> None:
    """验证复用查询传入 SHA/版本并检查分块数量一致。"""
    pool = _Pool()
    result = await find_reusable_document_index_run(pool, "waky3", "abc123", "v2")
    assert result["chunk_count"] == 20
    assert pool.args == ("waky3", "abc123", "v2")
    assert "COUNT(c.id) = r.chunk_count" in pool.query
    assert "r.status = 'COMPLETED'" in pool.query
