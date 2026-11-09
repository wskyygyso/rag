"""统一文档分块索引脚本测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import document_index


@pytest.mark.asyncio
async def test_document_index_script_dry_run_does_not_connect_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证分块索引脚本 dry-run 只扫描文件。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
        - `monkeypatch`：pytest 运行期替换工具，类型为 `pytest.MonkeyPatch`。
    """
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "README.md").write_text("guild invite", encoding="utf-8")
    monkeypatch.setattr(
        document_index,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root=str(tmp_path)),
    )
    assert await document_index.run_document_index_chunks("waky3", dry_run=True) == 1


def test_repository_revision_returns_none_for_non_git_directory(tmp_path: Path) -> None:
    """验证非 Git 项目不会阻断索引，版本标识返回空值。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。
    """
    assert document_index._repository_revision(str(tmp_path), "waky3") is None


@pytest.mark.asyncio
async def test_document_index_script_records_success(monkeypatch) -> None:
    """验证正式索引成功后写入完成状态和分块数量。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    pool = SimpleNamespace(closed=False)
    statuses = []

    async def close() -> None:
        """记录连接池关闭。"""
        pool.closed = True

    async def start(current_pool, project, *args):
        """返回模拟运行 ID。

        参数:
            - `current_pool`：当前步骤使用的数据库连接池。
            - `project`：项目标识。
            - `*args`：可变位置参数集合。
        """
        return 7

    async def index(root, project, current_pool):
        """返回模拟分块数量。

        参数:
            - `root`：仓库根目录。
            - `project`：项目标识。
            - `current_pool`：当前步骤使用的数据库连接池。
        """
        return 12

    async def finish(current_pool, run_id, status, count=0, error=None):
        """记录索引完成状态。

        参数:
            - `current_pool`：当前步骤使用的数据库连接池。
            - `run_id`：运行记录 ID。
            - `status`：状态值。
            - `count`：记录或结果数量。
            - `error`：失败原因或数据库异常。
        """
        statuses.append((run_id, status, count, error))

    pool.close = close
    monkeypatch.setattr(
        document_index,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root="/repositories"),
    )
    monkeypatch.setattr(document_index, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(document_index, "start_document_index_run_with_revision", start)
    monkeypatch.setattr(document_index, "recover_stale_document_index_runs", _recover)
    monkeypatch.setattr(document_index, "cleanup_document_index_runs", _cleanup)
    monkeypatch.setattr(document_index, "index_repository_documents", index)
    monkeypatch.setattr(document_index, "finish_document_index_run", finish)

    assert await document_index.run_document_index_chunks("waky3") == 12
    assert statuses == [(7, "COMPLETED", 12, None)]
    assert pool.closed is True


@pytest.mark.asyncio
async def test_document_index_script_records_failure(monkeypatch) -> None:
    """验证索引异常后写入失败状态并释放连接池。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    pool = SimpleNamespace(closed=False)
    statuses = []

    async def close() -> None:
        """记录连接池关闭。"""
        pool.closed = True

    async def start(current_pool, project, *args):
        """返回模拟运行 ID。

        参数:
            - `current_pool`：当前步骤使用的数据库连接池。
            - `project`：项目标识。
            - `*args`：可变位置参数集合。
        """
        return 8

    async def index(root, project, current_pool):
        """模拟索引异常。

        参数:
            - `root`：仓库根目录。
            - `project`：项目标识。
            - `current_pool`：当前步骤使用的数据库连接池。
        """
        raise RuntimeError("index failed")

    async def finish(current_pool, run_id, status, count=0, error=None):
        """记录索引失败状态。

        参数:
            - `current_pool`：当前步骤使用的数据库连接池。
            - `run_id`：运行记录 ID。
            - `status`：状态值。
            - `count`：记录或结果数量。
            - `error`：失败原因或数据库异常。
        """
        statuses.append((run_id, status, count, error))

    pool.close = close
    monkeypatch.setattr(
        document_index,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root="/repositories"),
    )
    monkeypatch.setattr(document_index, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(document_index, "start_document_index_run_with_revision", start)
    monkeypatch.setattr(document_index, "recover_stale_document_index_runs", _recover)
    monkeypatch.setattr(document_index, "cleanup_document_index_runs", _cleanup)
    monkeypatch.setattr(document_index, "index_repository_documents", index)
    monkeypatch.setattr(document_index, "finish_document_index_run", finish)

    with pytest.raises(RuntimeError, match="index failed"):
        await document_index.run_document_index_chunks("waky3")
    assert statuses == [(8, "FAILED", 0, "index failed")]
    assert pool.closed is True


async def _recover(current_pool, project):
    """模拟恢复历史悬挂索引。

    参数:
        - `current_pool`：当前步骤使用的数据库连接池。
        - `project`：项目标识。
    """
    return 0


async def _cleanup(current_pool, project):
    """模拟清理历史运行记录。

    参数:
        - `current_pool`：当前步骤使用的数据库连接池。
        - `project`：项目标识。
    """
    return 0


async def _return(value):
    """为异步 mock 返回固定值。

    参数:
        - `value`：异步 mock 需要直接返回的固定对象。
    """
    return value
