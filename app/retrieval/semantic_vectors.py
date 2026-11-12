"""真实语义向量候选的只读检索服务。

该模块把用户问题转换成真实 embedding，在项目文档分块索引中检索相近源码，
再生成诊断工作流可消费的候选证据。相似度只负责发现文件位置，不能直接证明
业务规则；所有候选仍会回到当前源码读取、符号索引和调用链验证。
"""

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional

import asyncpg

from app.document_chunks import search_document_chunks
from app.embeddings import (
    EMBEDDING_MODEL,
    EmbeddingProviderError,
    embedding_index_version,
    get_configured_embedding_provider,
)

# 只有生产 PHP 源码文件可以作为强语义门禁依据。配置和文档仍可在后续轻量
# 检索中辅助定位，但不能仅凭向量相似度绕过“必须回到业务源码验证”的约束。
_SEMANTIC_SOURCE_EXTENSIONS = {".php", ".inc", ".phtml"}


@dataclass(frozen=True)
class SemanticVectorSearchResult:
    """描述一次真实语义向量候选检索的命中、审计摘要和降级原因。

    字段:
        - `hits`：已经过滤相似度和源码类型的候选，可直接进入通用命中排序。
        - `summary`：不包含密钥或源码正文的检索通道摘要，供报告和监控使用。
        - `warnings`：需要向用户说明的配置缺失、索引缺口或外部服务降级。
    """

    hits: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _clamped_float(value: Any, default: float) -> float:
    """把配置值转换为 0 到 1 之间的相似度阈值。

    参数:
        - `value`：来自应用配置的阈值，可能是字符串或空值，类型为 `Any`。
        - `default`：配置不可解析时保留的业务默认阈值，类型为 `float`。

    返回:
        合法相似度阈值；非法值回退默认值，避免错误配置关闭全部候选或产生
        超出余弦相似度范围的门槛。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, result))


def _positive_int(value: Any, default: int, maximum: int) -> int:
    """把配置值转换为受上限保护的正整数。

    参数:
        - `value`：来自应用配置的数量或扫描上限，类型为 `Any`。
        - `default`：配置不可解析时使用的基础值，类型为 `int`。
        - `maximum`：单次检索允许的最大值，类型为 `int`，用于保护内存和延迟。
    """
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(maximum, max(1, result))


def _source_vector_hit(
    project: str,
    row: Dict[str, Any],
    *,
    min_similarity: float,
    strong_similarity: float,
) -> Optional[Dict[str, Any]]:
    """把一条兼容的文档分块转换为源码诊断候选。

    参数:
        - `project`：当前诊断项目标识，类型为 `str`，用于保持项目检索隔离。
        - `row`：`search_document_chunks` 返回的分块记录，类型为 `Dict[str, Any]`；
          必须包含向量兼容标记、源码路径、行范围和原始内容。
        - `min_similarity`：候选最低余弦相似度，类型为 `float`。
        - `strong_similarity`：可绕过硬锚点门禁的强候选门槛，类型为 `float`。

    Returns:
        满足真实模型兼容、源码类型和相似度门槛的候选；否则返回 `None`。候选只
        携带检索元数据，不在这里生成任何业务事实或答案。
    """
    file_name = str(row.get("file") or row.get("file_path") or "").replace("\\", "/").strip("/")
    extension = f".{file_name.rsplit('.', 1)[-1].lower()}" if "." in file_name else ""
    document_source_type = row.get("document_source_type")
    if document_source_type is None:
        # 兼容单元测试和后续 provider 直接返回的候选；生产搜索会同时给出
        # `source_type=chunk` 与原始 `document_source_type`，优先使用后者。
        document_source_type = row.get("source_type")
    if (
        not file_name
        or extension not in _SEMANTIC_SOURCE_EXTENSIONS
        or document_source_type != "code"
        or row.get("vector_compatible") is not True
        or row.get("semantic_vector") is not True
    ):
        return None
    try:
        vector_score = float(row.get("vector_score") or 0.0)
        lexical_score = float(row.get("lexical_score") or 0.0)
        line_start = max(1, int(row.get("line_start") or 1))
        line_end = max(line_start, int(row.get("line_end") or line_start))
        embedding_dimension = int(row.get("embedding_dimension") or 0)
    except (TypeError, ValueError):
        return None
    if vector_score < min_similarity:
        return None
    strong = vector_score >= strong_similarity
    return {
        "source_type": "code",
        "source_id": str(row.get("source_id") or f"{file_name}:{line_start}"),
        "project": project,
        "type": "generic_code",
        "file": file_name,
        "line": line_start,
        "line_start": line_start,
        "line_end": line_end,
        "content": str(row.get("content") or ""),
        "score": round(vector_score, 6),
        "vector_score": round(vector_score, 6),
        "lexical_score": round(lexical_score, 6),
        "semantic_vector": True,
        "vector_compatible": True,
        "semantic_vector_strong": strong,
        "embedding_provider": "openai_compatible",
        "embedding_model": str(row.get("embedding_model") or ""),
        "embedding_dimension": embedding_dimension,
        "retrieval_stage": "semantic_vector",
        "matched_queries": [],
        "matched_concepts": [],
    }


async def search_semantic_vector_candidates(
    pool: asyncpg.Pool,
    project: str,
    question: str,
    settings: Any,
) -> SemanticVectorSearchResult:
    """使用真实 embedding 检索项目源码候选并返回可审计的过滤结果。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool`；查询只读取当前
          项目的分块索引，不访问目标业务数据库。
        - `project`：已经通过接口访问控制的诊断项目标识，类型为 `str`。
        - `question`：用户业务问题，类型为 `str`；文本只发送给配置的 embedding
          服务生成查询向量，不拼入 SQL 或写入日志。
        - `settings`：应用运行配置，类型为 `Any`；决定真实 embedding 开关、
          模型、索引扫描边界和相似度门槛。

    Returns:
        候选命中、通道摘要和降级说明。未启用真实 embedding 时返回 `disabled`；
        外部服务失败或索引无命中时降级为空候选，不伪造哈希语义结果。
    """
    started_at = perf_counter()
    summary: Dict[str, Any] = {
        "channel": "semantic_vector",
        "status": "disabled",
        "hit_count": 0,
        "candidate_count": 0,
        "rejected_count": 0,
        "elapsed_ms": 0.0,
        "semantic_vector": False,
    }
    if not bool(getattr(settings, "embedding_enabled", False)):
        summary["elapsed_ms"] = round((perf_counter() - started_at) * 1000, 3)
        summary["reason"] = "真实语义向量检索未启用"
        return SemanticVectorSearchResult(summary=summary)

    try:
        provider = get_configured_embedding_provider(settings)
    except (EmbeddingProviderError, TypeError, ValueError) as exc:
        summary.update(status="degraded", reason="embedding 配置无效")
        summary["elapsed_ms"] = round((perf_counter() - started_at) * 1000, 3)
        return SemanticVectorSearchResult(
            summary=summary,
            warnings=[f"真实语义向量配置无效: {exc}"],
        )

    if provider.model_name == EMBEDDING_MODEL:
        summary.update(status="degraded", reason="离线哈希向量不能作为真实语义检索")
        summary["elapsed_ms"] = round((perf_counter() - started_at) * 1000, 3)
        return SemanticVectorSearchResult(
            summary=summary,
            warnings=["真实语义向量检索已拒绝离线哈希向量，请配置 embedding 模型"],
        )

    min_similarity = _clamped_float(
        getattr(settings, "semantic_vector_min_similarity", 0.30),
        0.30,
    )
    strong_similarity = _clamped_float(
        getattr(settings, "semantic_vector_strong_similarity", 0.40),
        0.40,
    )
    strong_similarity = max(min_similarity, strong_similarity)
    candidate_limit = _positive_int(
        getattr(settings, "semantic_vector_candidate_limit", 40),
        40,
        100,
    )
    scan_limit = _positive_int(
        getattr(settings, "semantic_vector_scan_limit", 20000),
        20000,
        100000,
    )
    summary.update(
        embedding_model=provider.model_name,
        embedding_dimension=provider.dimension,
        index_version=embedding_index_version(provider),
        min_similarity=min_similarity,
        strong_similarity=strong_similarity,
        candidate_limit=candidate_limit,
        scan_limit=scan_limit,
        semantic_vector=True,
    )
    try:
        rows = await search_document_chunks(
            pool,
            project,
            question,
            limit=min(200, max(candidate_limit * 3, candidate_limit)),
            embedding_provider=provider,
            scan_limit=scan_limit,
            compatible_only=True,
            source_types=("code",),
        )
    except (EmbeddingProviderError, OSError, ValueError) as exc:
        summary.update(status="degraded", reason="真实语义向量检索失败")
        summary["elapsed_ms"] = round((perf_counter() - started_at) * 1000, 3)
        return SemanticVectorSearchResult(
            summary=summary,
            warnings=[f"真实语义向量检索降级: {exc}"],
        )
    except Exception:
        summary.update(status="degraded", reason="语义向量索引查询失败")
        summary["elapsed_ms"] = round((perf_counter() - started_at) * 1000, 3)
        return SemanticVectorSearchResult(
            summary=summary,
            warnings=["真实语义向量检索降级: 语义索引查询失败"],
        )

    accepted: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        hit = _source_vector_hit(
            project,
            row,
            min_similarity=min_similarity,
            strong_similarity=strong_similarity,
        )
        if hit is None:
            continue
        key = (str(hit["file"]), int(hit["line_start"]), int(hit["line_end"]))
        if key in seen:
            continue
        seen.add(key)
        accepted.append(hit)
        if len(accepted) >= candidate_limit:
            break

    accepted.sort(
        key=lambda item: (
            -bool(item.get("semantic_vector_strong")),
            -float(item.get("vector_score") or 0.0),
            str(item.get("file") or ""),
            int(item.get("line_start") or 0),
        )
    )
    summary.update(
        status="completed" if accepted else "empty",
        hit_count=len(accepted),
        candidate_count=len(rows),
        rejected_count=max(0, len(rows) - len(accepted)),
        strong_hit_count=sum(
            1 for item in accepted if item.get("semantic_vector_strong") is True
        ),
        best_similarity=max(
            (float(item.get("vector_score") or 0.0) for item in accepted),
            default=0.0,
        ),
        elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
    )
    warnings = []
    if not accepted:
        warnings.append(
            "真实语义向量索引未返回可用源码候选，请确认已使用当前 embedding 模型重建项目索引"
        )
    return SemanticVectorSearchResult(hits=accepted, summary=summary, warnings=warnings)


__all__ = ["SemanticVectorSearchResult", "search_semantic_vector_candidates"]
