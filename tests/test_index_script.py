"""PHP 符号索引脚本的执行流程测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import index_php_repository


@pytest.mark.asyncio
async def test_index_script_dry_run_does_not_connect_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 dry-run 只扫描代码，不创建数据库连接。"""
    project = tmp_path / "waky3" / "php"
    project.mkdir(parents=True)
    (project / "Guild.php").write_text("<?php class Guild { public function invite() {} }", encoding="utf-8")
    monkeypatch.setattr(
        index_php_repository,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root=str(tmp_path)),
    )
    count = await index_php_repository._run("waky3", dry_run=True)
    assert count == 2


@pytest.mark.asyncio
async def test_index_script_closes_database_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证索引写入结束后一定释放连接池资源。"""
    project = tmp_path / "waky3" / "php"
    project.mkdir(parents=True)
    (project / "Guild.php").write_text("<?php class Guild {}", encoding="utf-8")
    pool = SimpleNamespace(closed=False)

    async def close() -> None:
        pool.closed = True

    pool.close = close
    monkeypatch.setattr(
        index_php_repository,
        "get_settings",
        lambda: SimpleNamespace(code_repositories_root=str(tmp_path)),
    )
    monkeypatch.setattr(index_php_repository, "create_pool", lambda settings: _return(pool))
    monkeypatch.setattr(
        index_php_repository,
        "replace_symbol_index",
        lambda current_pool, name, index: _return(len(index["symbols"])),
    )
    count = await index_php_repository._run("waky3")
    assert count == 1
    assert pool.closed is True


async def _return(value):
    """为异步 mock 返回固定值。"""
    return value
