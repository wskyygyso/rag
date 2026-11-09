"""文档索引按仓库版本复用测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import document_index


@pytest.mark.asyncio
async def test_document_index_reuses_complete_same_revision(monkeypatch, tmp_path: Path) -> None:
    """验证同 Git SHA 和索引版本且分块完整时跳过重建。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    pool = SimpleNamespace()

    async def close():
        """模拟关闭连接池。"""
        return None

    async def reusable(current_pool, project, revision, version):
        """返回可复用的运行记录。

        参数:
            - `current_pool`：当前步骤使用的数据库连接池。
            - `project`：项目标识。
            - `revision`：仓库版本。
            - `version`：索引版本标识。
        """
        assert (project, revision, version) == ("waky3", "abc123", "v1")
        return {"chunk_count": 42}

    async def should_not_index(*args, **kwargs):
        """复用后不应执行索引。

        参数:
            - `*args`：可变位置参数集合。
            - `**kwargs`：按关键字传入的附加参数集合。
        """
        raise AssertionError("不应重建索引")

    pool.close = close
    monkeypatch.setattr(
        document_index,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root=str(tmp_path)),
    )
    monkeypatch.setattr(document_index, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(document_index, "_repository_revision", lambda root, project: "abc123")
    monkeypatch.setattr(document_index, "recover_stale_document_index_runs", _recover)
    monkeypatch.setattr(document_index, "find_reusable_document_index_run", reusable)
    monkeypatch.setattr(document_index, "index_repository_documents", should_not_index)

    result = await document_index.run_document_index("waky3")
    assert result.chunk_count == 42
    assert result.reused is True


async def _recover(current_pool, project):
    """模拟无悬挂索引。

    参数:
        - `current_pool`：当前步骤使用的数据库连接池。
        - `project`：项目标识。
    """
    return 0


async def _return(value):
    """为异步 monkeypatch 返回固定值。

    参数:
        - `value`：异步 monkeypatch 需要直接返回的固定对象。
    """
    return value
