"""全文和向量分块召回测试。"""

import pytest

from app.document_chunks import search_document_chunks
from app.embeddings import embed_text


class _Pool:
    """返回分块全文和向量字段的连接池替身。"""

    async def fetch(self, query, project, keyword):
        """返回一条与查询高度相似的分块。"""
        return [
            {
                "source_type": "code",
                "source_id": "Guild.php:1-3",
                "file_path": "Guild.php",
                "line_start": 1,
                "line_end": 3,
                "content": "Guild invite failed",
                "embedding": embed_text("Guild invite failed"),
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
