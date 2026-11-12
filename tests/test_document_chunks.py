"""统一文档分块工具测试。"""

import time

import pytest

from app.document_chunks import (
    build_repository_chunks,
    bulk_replace_document_chunks,
    chunk_text,
    embedding_text_for_chunk,
)
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


def test_chunk_text_records_injected_real_embedding_metadata() -> None:
    """验证注入真实 provider 后分块记录对应模型、维度和向量。"""
    class RealProvider:
        """返回固定三维向量的真实 provider 测试替身。"""

        model_name = "semantic-test"
        dimension = 3

        def embed(self, text):
            """返回固定单位向量。

            参数:
                - `text`：待生成向量的分块内容。
            """
            return [1.0, 0.0, 0.0]

        def embed_many(self, texts):
            """批量返回固定向量并保持输入数量。

            参数:
                - `texts`：同一批待向量化文本。
            """
            return [self.embed(text) for text in texts]

    chunks = chunk_text(
        "waky6",
        "code",
        "RegistrationService.php",
        "<?php class RegistrationService {}",
        max_chars=100,
        embedding_provider=RealProvider(),
    )

    assert chunks[0]["embedding_model"] == "semantic-test"
    assert chunks[0]["embedding_dimension"] == 3
    assert chunks[0]["embedding"] == [1.0, 0.0, 0.0]


def test_code_embedding_text_expands_identifiers_without_business_mapping() -> None:
    """验证代码向量输入会把命名形式拆成英文词，并保留真实中文注释。"""
    chunk = {
        "source_type": "code",
        "file_path": "DeviceRegistrationService.php",
        "content": "<?php class DeviceRegistrationService { public function register() { if ($deviceUserCount >= $maxUsersPerDevice) { /* 设备注册上限 */ } } }",
    }

    text = embedding_text_for_chunk(chunk)

    assert "device registration service" in text
    assert "user count" in text
    assert "max users per" in text
    assert "设备注册上限" in text
    assert "class" not in text.split("Chinese labels:")[0].split()


def test_document_embedding_text_keeps_original_content() -> None:
    """验证普通文档继续使用原文，不会被代码标识符规则改写。"""
    chunk = {"source_type": "document", "content": "提现最低金额说明"}

    assert embedding_text_for_chunk(chunk) == "提现最低金额说明"


def test_chunk_text_parallel_embedding_keeps_source_order() -> None:
    """验证并发批次即使乱序完成，也不会把向量写入错误源码分块。"""
    class ParallelProvider:
        """按文本内容生成可校验向量，并让不同批次以不同速度完成。"""

        model_name = "parallel-semantic"
        dimension = 1
        batch_size = 1
        concurrency = 3

        def embed_many(self, texts):
            """为单条文本生成可验证向量。

            参数:
                - `texts`：当前批次的一条源码或文档文本。
            """
            values = list(texts)
            if values and "line03" in values[0]:
                time.sleep(0.02)
            return [[float(sum(ord(character) for character in text) % 100_003)] for text in values]

    content = "\n".join(f"line{index:02d}" for index in range(1, 61))
    chunks = chunk_text(
        "waky6",
        "code",
        "ParallelService.php",
        content,
        max_chars=100,
        overlap_lines=0,
        embedding_provider=ParallelProvider(),
    )

    for chunk in chunks:
        embedding_text = embedding_text_for_chunk(chunk)
        expected = float(sum(ord(character) for character in embedding_text) % 100_003)
        assert chunk["embedding"] == [expected]


@pytest.mark.asyncio
async def test_bulk_replace_document_chunks_uses_atomic_copy() -> None:
    """验证项目全量索引在事务内删除后使用 COPY 写入，而非逐行往返。"""
    events = []

    class Transaction:
        """记录事务边界。"""

        async def __aenter__(self):
            """进入事务并记录开始。"""
            events.append("begin")

        async def __aexit__(self, exc_type, exc, traceback):
            """退出事务并记录提交或回滚。

            参数:
                - `exc_type`：事务内异常类型。
                - `exc`：事务内异常实例。
                - `traceback`：异常回溯对象。
            """
            events.append("rollback" if exc_type else "commit")

    class Connection:
        """模拟执行 DELETE 和 COPY 的数据库连接。"""

        def transaction(self):
            """返回事务上下文。"""
            return Transaction()

        async def execute(self, sql, project):
            """记录项目删除语句。

            参数:
                - `sql`：删除旧分块的 SQL。
                - `project`：当前项目。
            """
            events.append(("delete", sql, project))

        async def copy_records_to_table(self, table, *, columns, records):
            """记录批量写入的表、字段和记录数。

            参数:
                - `table`：目标表名。
                - `columns`：显式列顺序。
                - `records`：待写入记录。
            """
            events.append(("copy", table, columns, len(records)))

    class Acquire:
        """模拟 asyncpg 连接池的异步上下文管理器。"""

        async def __aenter__(self):
            """返回数据库连接替身。"""
            return Connection()

        async def __aexit__(self, exc_type, exc, traceback):
            """连接上下文无需清理。

            参数:
                - `exc_type`：连接使用期间异常类型。
                - `exc`：连接使用期间异常实例。
                - `traceback`：异常回溯对象。
            """
            return False

    class Pool:
        """模拟连接池获取接口。"""

        def acquire(self):
            """返回连接上下文。"""
            return Acquire()

    chunk = chunk_text(
        "waky6",
        "code",
        "RegistrationService.php",
        "<?php class RegistrationService {}",
        embed=False,
    )[0]
    chunk.update({"embedding": [1.0, 0.0], "embedding_model": "semantic", "embedding_dimension": 2})

    count = await bulk_replace_document_chunks(Pool(), "waky6", [chunk])

    assert count == 1
    assert events[0] == "begin"
    assert events[1][0] == "delete"
    assert events[2][0] == "copy"
    assert events[2][3] == 1
    assert events[3] == "commit"
