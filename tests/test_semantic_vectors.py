"""真实语义向量候选服务测试。"""

from types import SimpleNamespace

import pytest

from app.embeddings import DeterministicEmbeddingProvider, EmbeddingProviderError
from app.retrieval import semantic_vectors


class _RealProvider:
    """模拟与已建索引兼容的远程 embedding provider。"""

    model_name = "multilingual-semantic-v2"
    dimension = 1024

    def embed(self, text: str):
        """返回固定查询向量，测试只关注候选过滤而不访问外部服务。

        参数:
            - `text`：待生成向量的用户问题。
        """
        return [1.0] + [0.0] * 1023

    def embed_many(self, texts):
        """逐条返回固定查询向量。

        参数:
            - `texts`：需要生成向量的文本集合。
        """
        return [self.embed(text) for text in texts]


def _settings(**overrides):
    """构造真实向量检索所需的最小配置。

    参数:
        - `**overrides`：测试需要覆盖的 embedding 和门槛字段。
    """
    values = {
        "embedding_enabled": True,
        "semantic_vector_min_similarity": 0.32,
        "semantic_vector_strong_similarity": 0.45,
        "semantic_vector_candidate_limit": 40,
        "semantic_vector_scan_limit": 20000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_semantic_vectors_disabled_do_not_use_hash_vectors(monkeypatch) -> None:
    """验证关闭真实 embedding 时不会把离线 hash 命中送入诊断。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    async def should_not_search(*args, **kwargs):
        """真实向量开关关闭时不应查询分块索引。

        参数:
            - `*args`：检索函数位置参数。
            - `**kwargs`：检索函数关键字参数。
        """
        raise AssertionError("关闭真实 embedding 后不应检索向量")

    monkeypatch.setattr(semantic_vectors, "search_document_chunks", should_not_search)
    result = await semantic_vectors.search_semantic_vector_candidates(
        object(),
        "waky6",
        "未知业务问题",
        _settings(embedding_enabled=False),
    )

    assert result.hits == []
    assert result.summary["status"] == "disabled"
    assert result.warnings == []


@pytest.mark.asyncio
async def test_semantic_vectors_keep_only_compatible_source_candidates(monkeypatch) -> None:
    """验证只保留真实模型兼容的 PHP 源码候选，并标记强相似度。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    rows = [
        {
            # search_document_chunks 对外返回的通用结果类型固定为 chunk；真实来源
            # 在 document_source_type 中保留，过滤逻辑不能因此丢掉所有源码候选。
            "source_type": "chunk",
            "document_source_type": "code",
            "source_id": "php/service/RegistrationService.php:20-90",
            "file_path": "php/service/RegistrationService.php",
            "line_start": 20,
            "line_end": 90,
            "content": "if ($deviceUserCount >= $limit) { throw new Exception(); }",
            "vector_score": 0.61,
            "lexical_score": 0.0,
            "semantic_vector": True,
            "vector_compatible": True,
            "embedding_model": "multilingual-semantic-v2",
            "embedding_dimension": 1024,
        },
        {
            "source_type": "document",
            "source_id": "README.md:1-20",
            "file_path": "README.md",
            "line_start": 1,
            "line_end": 20,
            "content": "device registration documentation",
            "vector_score": 0.9,
            "semantic_vector": True,
            "vector_compatible": True,
            "embedding_model": "multilingual-semantic-v2",
            "embedding_dimension": 1024,
        },
        {
            "source_type": "code",
            "source_id": "php/old.php:1-20",
            "file_path": "php/old.php",
            "line_start": 1,
            "line_end": 20,
            "content": "legacy vector",
            "vector_score": 0.9,
            "semantic_vector": False,
            "vector_compatible": False,
            "embedding_model": "legacy-v0",
            "embedding_dimension": 64,
        },
        {
            "source_type": "code",
            "source_id": "php/weak.php:1-20",
            "file_path": "php/weak.php",
            "line_start": 1,
            "line_end": 20,
            "content": "weak similarity",
            "vector_score": 0.2,
            "semantic_vector": True,
            "vector_compatible": True,
            "embedding_model": "multilingual-semantic-v2",
            "embedding_dimension": 1024,
        },
    ]

    async def fake_search(pool, project, question, **kwargs):
        """返回不同来源和兼容状态的分块，验证服务过滤边界。

        参数:
            - `pool`：测试连接池。
            - `project`：当前项目标识。
            - `question`：用户问题。
            - `**kwargs`：真实 provider、结果数量和扫描上限。
        """
        assert kwargs["embedding_provider"].model_name == "multilingual-semantic-v2"
        assert kwargs["compatible_only"] is True
        assert kwargs["source_types"] == ("code",)
        return rows

    monkeypatch.setattr(semantic_vectors, "get_configured_embedding_provider", lambda settings: _RealProvider())
    monkeypatch.setattr(semantic_vectors, "search_document_chunks", fake_search)

    result = await semantic_vectors.search_semantic_vector_candidates(
        object(),
        "waky6",
        "同一设备最多注册多少账号",
        _settings(),
    )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit["file"] == "php/service/RegistrationService.php"
    assert hit["retrieval_stage"] == "semantic_vector"
    assert hit["semantic_vector_strong"] is True
    assert hit["vector_compatible"] is True
    assert hit["embedding_provider"] == "openai_compatible"
    assert hit["matched_queries"] == []
    assert "conclusion" not in hit
    assert result.summary["status"] == "completed"
    assert result.summary["strong_hit_count"] == 1


@pytest.mark.asyncio
async def test_semantic_vectors_reject_offline_provider(monkeypatch) -> None:
    """验证即使开启开关，hash provider 也不能冒充真实语义检索。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    monkeypatch.setattr(
        semantic_vectors,
        "get_configured_embedding_provider",
        lambda settings: DeterministicEmbeddingProvider(),
    )

    result = await semantic_vectors.search_semantic_vector_candidates(
        object(),
        "waky6",
        "设备注册上限",
        _settings(),
    )

    assert result.hits == []
    assert result.summary["status"] == "degraded"
    assert "离线哈希向量" in result.warnings[0]


@pytest.mark.asyncio
async def test_semantic_vector_provider_failure_degrades_without_hits(monkeypatch) -> None:
    """验证 embedding 服务失败时返回受控降级，不产生伪造候选。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    def broken_provider(settings):
        """模拟 embedding 配置或鉴权失败。

        参数:
            - `settings`：当前应用配置。
        """
        raise EmbeddingProviderError("embedding 请求超时")

    monkeypatch.setattr(semantic_vectors, "get_configured_embedding_provider", broken_provider)
    result = await semantic_vectors.search_semantic_vector_candidates(
        object(),
        "waky6",
        "设备注册上限",
        _settings(),
    )

    assert result.hits == []
    assert result.summary["status"] == "degraded"
    assert "超时" in result.warnings[0]
