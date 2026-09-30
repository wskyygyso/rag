"""统一文档分块索引脚本测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import index_document_repository


@pytest.mark.asyncio
async def test_document_index_script_dry_run_does_not_connect_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证分块索引脚本 dry-run 只扫描文件。"""
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "README.md").write_text("guild invite", encoding="utf-8")
    monkeypatch.setattr(
        index_document_repository,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root=str(tmp_path)),
    )
    assert await index_document_repository._run("waky3", dry_run=True) == 1


def test_repository_revision_returns_none_for_non_git_directory(tmp_path: Path) -> None:
    """验证非 Git 项目不会阻断索引，版本标识返回空值。"""
    assert index_document_repository._repository_revision(str(tmp_path), "waky3") is None


@pytest.mark.asyncio
async def test_document_index_script_records_success(monkeypatch) -> None:
    """验证正式索引成功后写入完成状态和分块数量。"""
    pool = SimpleNamespace(closed=False)
    statuses = []

    async def close() -> None:
        """记录连接池关闭。"""
        pool.closed = True

    async def start(current_pool, project, *args):
        """返回模拟运行 ID。"""
        return 7

    async def index(root, project, current_pool):
        """返回模拟分块数量。"""
        return 12

    async def finish(current_pool, run_id, status, count=0, error=None):
        """记录索引完成状态。"""
        statuses.append((run_id, status, count, error))

    pool.close = close
    monkeypatch.setattr(
        index_document_repository,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root="/repositories"),
    )
    monkeypatch.setattr(index_document_repository, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(index_document_repository, "start_document_index_run_with_revision", start)
    monkeypatch.setattr(index_document_repository, "recover_stale_document_index_runs", _recover)
    monkeypatch.setattr(index_document_repository, "index_repository_documents", index)
    monkeypatch.setattr(index_document_repository, "finish_document_index_run", finish)

    assert await index_document_repository._run("waky3") == 12
    assert statuses == [(7, "COMPLETED", 12, None)]
    assert pool.closed is True


@pytest.mark.asyncio
async def test_document_index_script_records_failure(monkeypatch) -> None:
    """验证索引异常后写入失败状态并释放连接池。"""
    pool = SimpleNamespace(closed=False)
    statuses = []

    async def close() -> None:
        """记录连接池关闭。"""
        pool.closed = True

    async def start(current_pool, project, *args):
        """返回模拟运行 ID。"""
        return 8

    async def index(root, project, current_pool):
        """模拟索引异常。"""
        raise RuntimeError("index failed")

    async def finish(current_pool, run_id, status, count=0, error=None):
        """记录索引失败状态。"""
        statuses.append((run_id, status, count, error))

    pool.close = close
    monkeypatch.setattr(
        index_document_repository,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root="/repositories"),
    )
    monkeypatch.setattr(index_document_repository, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(index_document_repository, "start_document_index_run_with_revision", start)
    monkeypatch.setattr(index_document_repository, "recover_stale_document_index_runs", _recover)
    monkeypatch.setattr(index_document_repository, "index_repository_documents", index)
    monkeypatch.setattr(index_document_repository, "finish_document_index_run", finish)

    with pytest.raises(RuntimeError, match="index failed"):
        await index_document_repository._run("waky3")
    assert statuses == [(8, "FAILED", 0, "index failed")]
    assert pool.closed is True


async def _recover(current_pool, project):
    """模拟恢复历史悬挂索引。"""
    return 0


async def _return(value):
    """为异步 mock 返回固定值。"""
    return value
