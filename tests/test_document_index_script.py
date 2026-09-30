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
