"""统一文档分块工具测试。"""

import pytest

from app.document_chunks import build_repository_chunks, chunk_text
from app.embeddings import EMBEDDING_DIMENSION, EMBEDDING_MODEL, cosine_similarity, embed_text, get_embedding_provider
from app.embeddings import EMBEDDING_MODEL


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
    assert len(chunks[0]["embedding"]) == EMBEDDING_DIMENSION
    assert chunks[0]["embedding_model"] == EMBEDDING_MODEL
    assert chunks[0]["embedding_dimension"] == EMBEDDING_DIMENSION


def test_chunk_text_rejects_invalid_boundaries() -> None:
    """验证分块来源和大小参数边界。"""
    with pytest.raises(ValueError):
        chunk_text("", "code", "id", "content")
    with pytest.raises(ValueError):
        chunk_text("waky3", "code", "id", "content", max_chars=99)
    with pytest.raises(ValueError):
        chunk_text("waky3", "code", "id", "content", overlap_lines=21)


def test_chunk_text_splits_single_line_longer_than_database_limit() -> None:
    """验证超长单行被拆成来源唯一且长度受限的字符分块。"""
    chunks = chunk_text("waky3", "document", "large.json", "x" * 1200, max_chars=500)
    assert len(chunks) == 3
    assert all(len(chunk["content"]) <= 500 for chunk in chunks)
    assert len({chunk["source_id"] for chunk in chunks}) == 3
    assert {chunk["line_start"] for chunk in chunks} == {1}


def test_build_repository_chunks_skips_dependencies_and_classifies_sources(tmp_path) -> None:
    """验证仓库扫描跳过依赖目录，并区分 PHP 代码和普通文档。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    project = tmp_path / "waky3"
    (project / "src").mkdir(parents=True)
    (project / "vendor").mkdir()
    (project / "src" / "Guild.php").write_text("<?php\nclass Guild {}", encoding="utf-8")
    (project / "README.md").write_text("guild invite docs", encoding="utf-8")
    (project / "vendor" / "Ignored.php").write_text("class Ignored {}", encoding="utf-8")
    chunks = build_repository_chunks(str(tmp_path), "waky3", max_chars=100)
    assert {chunk["source_type"] for chunk in chunks} == {"code", "document"}
    assert all("vendor" not in chunk["file_path"] for chunk in chunks)


def test_build_repository_chunks_skips_gitnexus_generated_cache(tmp_path) -> None:
    """验证 GitNexus 生成缓存不会进入项目代码索引。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    project = tmp_path / "waky3"
    source = project / "src"
    cache = project / ".gitnexus" / "parsedfile-cache"
    source.mkdir(parents=True)
    cache.mkdir(parents=True)
    (source / "Guild.php").write_text("<?php class Guild {}", encoding="utf-8")
    (cache / "large.json").write_text('{"content":"' + "x" * 2000 + '"}', encoding="utf-8")
    chunks = build_repository_chunks(str(tmp_path), "waky3", max_chars=100)
    assert {chunk["file_path"] for chunk in chunks} == {"src/Guild.php"}


def test_embedding_is_deterministic_and_similarity_is_bounded() -> None:
    """验证 MVP 向量稳定，余弦相似度保持在合法范围。"""
    first = embed_text("Guild invite failed")
    second = embed_text("Guild invite failed")
    other = embed_text("different question")
    assert first == second
    assert cosine_similarity(first, first) == pytest.approx(1.0)
    assert -1.0 <= cosine_similarity(first, other) <= 1.0


def test_embedding_provider_contract_rejects_unknown_model() -> None:
    """验证 provider 工厂能返回本地实现并拒绝未知模型。"""
    provider = get_embedding_provider()
    assert provider.model_name == EMBEDDING_MODEL
    assert len(provider.embed("invite")) == EMBEDDING_DIMENSION
    with pytest.raises(ValueError):
        get_embedding_provider("unknown-model")
