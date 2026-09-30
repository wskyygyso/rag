"""统一文档分块工具测试。"""

import pytest

from app.document_chunks import build_repository_chunks, chunk_text


def test_chunk_text_keeps_line_ranges_hashes_and_overlap() -> None:
    """验证分块保留行号、稳定哈希和上下文重叠。"""
    content = "\n".join(f"line{index:02d}" for index in range(1, 31))
    chunks = chunk_text(
        "waky3",
        "code",
        "Guild.php",
        content,
        file_path="Guild.php",
        max_chars=100,
        overlap_lines=1,
    )
    assert len(chunks) >= 2
    assert chunks[0]["line_start"] == 1
    assert chunks[0]["file_path"] == "Guild.php"
    assert chunks[0]["content_hash"] == chunk_text(
        "waky3", "code", "Guild.php", chunks[0]["content"], max_chars=100
    )[0]["content_hash"]
    assert chunks[1]["line_start"] <= chunks[0]["line_end"]


def test_chunk_text_rejects_invalid_boundaries() -> None:
    """验证分块来源和大小参数边界。"""
    with pytest.raises(ValueError):
        chunk_text("", "code", "id", "content")
    with pytest.raises(ValueError):
        chunk_text("waky3", "code", "id", "content", max_chars=99)
    with pytest.raises(ValueError):
        chunk_text("waky3", "code", "id", "content", overlap_lines=21)


def test_build_repository_chunks_skips_dependencies_and_classifies_sources(tmp_path) -> None:
    """验证仓库扫描跳过依赖目录，并区分 PHP 代码和普通文档。"""
    project = tmp_path / "waky3"
    (project / "src").mkdir(parents=True)
    (project / "vendor").mkdir()
    (project / "src" / "Guild.php").write_text("<?php\nclass Guild {}", encoding="utf-8")
    (project / "README.md").write_text("guild invite docs", encoding="utf-8")
    (project / "vendor" / "Ignored.php").write_text("class Ignored {}", encoding="utf-8")
    chunks = build_repository_chunks(str(tmp_path), "waky3", max_chars=100)
    assert {chunk["source_type"] for chunk in chunks} == {"code", "document"}
    assert all("vendor" not in chunk["file_path"] for chunk in chunks)
