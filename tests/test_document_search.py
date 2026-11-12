"""全文和向量分块召回测试。"""

import json

import pytest

from app.document_chunks import _decode_embedding_vector, search_document_chunks
from app.embeddings import embed_text


class _Pool:
    """返回分块全文和向量字段的连接池替身。"""

    async def fetch(self, sql, project, query, scan_limit, *embedding_filters):
        """返回一条与查询高度相似的分块。

        参数:
            - `sql`：分块检索 SQL；测试替身不执行数据库语句。
            - `project`：项目标识。
            - `query`：用户检索问题，用于说明当前测试场景。
            - `scan_limit`：允许扫描的分块数量上限。
            - `*embedding_filters`：兼容性过滤开启时传入的模型名和维度。
        """
        return [
            {
                "source_type": "code",
                "source_id": "Guild.php:1-3",
                "file_path": "Guild.php",
                "line_start": 1,
                "line_end": 3,
                "content": "Guild invite failed",
                "embedding": embed_text("Guild invite failed"),
                "embedding_model": "hash-v1",
                "embedding_dimension": 64,
                "lexical_score": 0.5,
            }
        ]


@pytest.mark.asyncio
async def test_search_document_chunks_combines_lexical_and_vector_scores() -> None:
    """验证分块召回同时输出全文、向量和融合分数。"""
    result = await search_document_chunks(_Pool(), "waky3", "Guild invite failed", limit=5)
    assert len(result) == 1
    assert result[0]["lexical_score"] == 0.5
    assert result[0]["vector_score"] > 0
    assert result[0]["score"] > 0
    assert result[0]["embedding_model"] == "hash-v1"
    assert result[0]["vector_compatible"] is True


@pytest.mark.asyncio
async def test_search_document_chunks_falls_back_to_lexical_for_incompatible_vector() -> None:
    """验证模型或维度不兼容时不混算向量分数，仍保留全文结果。"""
    class IncompatiblePool:
        """返回旧模型分块的连接池替身。"""

        async def fetch(self, sql, project, query, scan_limit):
            """返回旧模型向量和可用全文分数。

            参数:
                - `sql`：分块检索 SQL；测试替身不执行数据库语句。
                - `project`：项目标识。
                - `query`：用户检索问题，用于说明当前测试场景。
                - `scan_limit`：允许扫描的分块数量上限。
            """
            return [
                {
                    "source_id": "old:1-2",
                    "file_path": "old.md",
                    "line_start": 1,
                    "line_end": 2,
                    "content": "old content",
                    "embedding": embed_text("old content"),
                    "embedding_model": "legacy-v0",
                    "embedding_dimension": 32,
                    "lexical_score": 0.8,
                }
            ]

    result = await search_document_chunks(IncompatiblePool(), "waky3", "old", limit=5)
    assert result[0]["vector_score"] == 0
    assert result[0]["vector_compatible"] is False
    assert result[0]["score"] == 0.48


@pytest.mark.asyncio
async def test_search_document_chunks_filters_current_model_for_semantic_scan() -> None:
    """验证真实语义扫描在数据库层过滤当前模型和维度，并保留原始来源类型。"""
    class CompatiblePool:
        """记录兼容性过滤参数的连接池替身。"""

        async def fetch(self, sql, project, query, scan_limit, *embedding_filters):
            """返回一条真实模型源码分块。

            参数:
                - `sql`：语义检索 SQL；测试替身只验证过滤条件存在。
                - `project`：项目标识。
                - `query`：用户问题。
                - `scan_limit`：扫描上限。
                - `*embedding_filters`：当前索引要求的模型和维度。
            """
            assert "source_type = ANY($4::text[])" in sql
            assert "embedding_model = $5" in sql
            assert "embedding_dimension = $6" in sql
            assert embedding_filters == (["code"], "hash-v1", 64)
            return [
                {
                    "source_type": "code",
                    "source_id": "Guild.php:1-3",
                    "file_path": "Guild.php",
                    "line_start": 1,
                    "line_end": 3,
                    "content": "Guild invite failed",
                    "embedding": embed_text("Guild invite failed"),
                    "embedding_model": "hash-v1",
                    "embedding_dimension": 64,
                    "lexical_score": 0.5,
                }
            ]

    result = await search_document_chunks(
        CompatiblePool(),
        "waky3",
        "Guild invite failed",
        limit=5,
        compatible_only=True,
        source_types=("code",),
    )

    assert result[0]["source_type"] == "chunk"
    assert result[0]["document_source_type"] == "code"


@pytest.mark.asyncio
async def test_search_document_chunks_decodes_asyncpg_jsonb_embedding_string() -> None:
    """验证 asyncpg 返回 JSONB 字符串时仍能计算真实向量相似度。

    生产 asyncpg 连接未注册 JSON 编解码器时，`embedding` 字段是 JSON 文本而
    不是 Python 列表。该场景必须与测试替身返回列表时得到完全相同的召回结果，
    否则模型兼容的索引会在评分阶段静默变成零分。
    """

    class JsonbStringPool:
        """模拟 asyncpg 默认把 JSONB 向量读取为字符串的连接池替身。"""

        async def fetch(self, sql, project, query, scan_limit):
            """返回一条 embedding 字段为 JSON 文本的源码分块。

            参数:
                - `sql`：分块检索 SQL；测试替身不执行数据库语句。
                - `project`：项目标识。
                - `query`：用户检索问题。
                - `scan_limit`：允许扫描的分块数量上限。
            """
            return [
                {
                    "source_type": "code",
                    "source_id": "RegistrationService.php:1-3",
                    "file_path": "RegistrationService.php",
                    "line_start": 1,
                    "line_end": 3,
                    "content": "Guild invite failed",
                    "embedding": json.dumps(embed_text("Guild invite failed")),
                    "embedding_model": "hash-v1",
                    "embedding_dimension": 64,
                    "lexical_score": 0.0,
                }
            ]

    result = await search_document_chunks(
        JsonbStringPool(),
        "waky3",
        "Guild invite failed",
        limit=5,
    )

    assert len(result) == 1
    assert result[0]["vector_score"] > 0
    assert result[0]["vector_compatible"] is True
    assert result[0]["semantic_vector"] is True


def test_decode_embedding_vector_rejects_invalid_json_values() -> None:
    """验证损坏或非数值 JSONB 向量只丢弃当前向量，不制造错误相似度。"""
    assert _decode_embedding_vector("[1, 2.5]") == [1.0, 2.5]
    assert _decode_embedding_vector("not-json") is None
    assert _decode_embedding_vector('["1"]') is None
    assert _decode_embedding_vector("[true]") is None
    assert _decode_embedding_vector("[NaN]") is None
