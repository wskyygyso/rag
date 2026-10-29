"""文档索引按仓库版本复用测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import index_document_repository


@pytest.mark.asyncio
async def test_document_index_reuses_complete_same_revision(monkeypatch, tmp_path: Path) -> None:
    """验证同 Git SHA 和索引版本且分块完整时跳过重建。"""
    pool = SimpleNamespace()

    async def close():
        """模拟关闭连接池。"""
        return None

    async def reusable(current_pool, project, revision, version):
        """返回可复用的运行记录。"""
        assert (project, revision, version) == ("waky3", "abc123", "v1")
        return {"chunk_count": 42}

    async def should_not_index(*args, **kwargs):
        """复用后不应执行索引。"""
        raise AssertionError("不应重建索引")

    pool.close = close
    monkeypatch.setattr(
        index_document_repository,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root=str(tmp_path)),
    )
    monkeypatch.setattr(index_document_repository, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(index_document_repository, "_repository_revision", lambda root, project: "abc123")
    monkeypatch.setattr(index_document_repository, "recover_stale_document_index_runs", _recover)
    monkeypatch.setattr(index_document_repository, "find_reusable_document_index_run", reusable)
    monkeypatch.setattr(index_document_repository, "index_repository_documents", should_not_index)

    result = await index_document_repository._run_detailed("waky3")
    assert result.chunk_count == 42
    assert result.reused is True


async def _recover(current_pool, project):
    """模拟无悬挂索引。"""
    return 0


async def _return(value):
    """为异步 monkeypatch 返回固定值。"""
    return value
