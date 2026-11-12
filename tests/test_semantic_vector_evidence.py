"""真实语义向量候选进入诊断证据层的预算测试。"""

from app.diagnosis_workflow import _method_evidence_range, _select_diagnostic_evidence


def _semantic_hit(index: int) -> dict:
    """构造一条带完整分块范围的真实向量候选。

    参数:
        - `index`：候选序号，用于生成稳定文件和行号。
    """
    start = 20 + index * 100
    return {
        "type": "generic_code",
        "file": f"php/service/SemanticService{index}.php",
        "line": start,
        "line_start": start,
        "line_end": start + 70,
        "content": "if ($count >= $limit) { throw new Exception(); }",
        "retrieval_stage": "semantic_vector",
        "semantic_vector": True,
        "semantic_vector_strong": True,
        "vector_score": 0.62,
        "vector_compatible": True,
        "embedding_provider": "openai_compatible",
        "embedding_model": "multilingual-semantic-v2",
        "matched_queries": [],
        "matched_concepts": [],
    }


def test_semantic_vector_evidence_reads_full_indexed_chunk_range() -> None:
    """验证向量候选会读取索引分块的完整行范围，而不是只读分块首行附近。"""
    start_line, end_line = _method_evidence_range({}, _semantic_hit(0))

    assert (start_line, end_line) == (20, 90)


def test_semantic_vector_evidence_budget_is_capped_at_ten() -> None:
    """验证真实向量候选最多占十条最终证据，其余预算留给源码和调用链。"""
    selected = _select_diagnostic_evidence(
        [_semantic_hit(index) for index in range(20)],
        [],
        limit=64,
    )

    assert len(selected) == 10
    assert all(item["retrieval_stage"] == "semantic_vector" for item in selected)


def test_semantic_vector_hit_has_no_direct_business_conclusion_fields() -> None:
    """验证候选只携带检索元数据，不把相似度直接写成业务结论。"""
    hit = _semantic_hit(0)

    assert "conclusion" not in hit
    assert "facts" not in hit
    assert hit["vector_score"] > 0
