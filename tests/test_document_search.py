"""全文和向量分块召回测试。"""

import pytest

from app.document_chunks import search_document_chunks
from app.embeddings import embed_text


class _Pool:
    """返回分块全文和向量字段的连接池替身。"""

    async def fetch(self, query, project, keyword):
        """返回一条与查询高度相似的分块。

        参数:
            - `query`：检索或查询文本。
            - `project`：项目标识。
            - `keyword`：历史任务检索关键词。
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

        async def fetch(self, query, project, keyword):
            """返回旧模型向量和可用全文分数。

            参数:
                - `query`：检索或查询文本。
                - `project`：项目标识。
                - `keyword`：历史任务检索关键词。
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
