"""通用问题理解、概念组合和跨语言检索计划测试。"""

from app.query_understanding import (
    SEMANTIC_CONCEPTS,
    annotate_hits_with_concepts,
    build_semantic_query_plan,
    detect_semantic_concepts,
    infer_preferred_repository,
)


def test_semantic_concepts_compose_without_complete_question_mapping() -> None:
    """验证多个业务概念可以自由组合，而不是依赖完整问句模板。"""
    concepts = detect_semantic_concepts("钻石提现最小兑换金额是多少？有手续费嘛？")

    assert {"diamond", "withdrawal", "limit", "exchange", "amount", "fee"} <= {
        concept.name for concept in concepts
    }


def test_semantic_plan_reuses_concepts_for_different_questions() -> None:
    """验证同一组业务概念能服务不同问法，新增问句不需要复制实现符号。"""
    first = build_semantic_query_plan("钻石提现手续费是多少？")
    second = build_semantic_query_plan("提现钻石为什么扣手续费？")

    assert {"diamond", "withdrawal", "fee"} <= set(first["concepts"])
    assert {"diamond", "withdrawal", "fee"} <= set(second["concepts"])
    assert "diamond" in first["code_terms"]
    assert "withdraw" in first["code_terms"]
    assert "fee" in second["code_terms"]


def test_semantic_terms_contain_only_reusable_words() -> None:
    """验证概念层不保存项目、类、方法、表名或完整问题答案。"""
    plan = build_semantic_query_plan(
        "waky4 排查前端展示钻石是根据什么？",
        seed_hits=[
            {
                "file": "waky4_miyouphp/php/action/DiamondControl.php",
                "content": "// 主播钻石档位与前端展示",
            }
        ],
    )
    serialized = repr(plan).casefold()

    assert "waky4" not in serialized
    assert "diamondcontrol" not in serialized
    assert "getdiamondgearlist" not in serialized
    assert "t_user_diamonds" not in serialized
    assert {"display", "diamond", "anchor", "gear"} <= set(plan["concepts"])


def test_unknown_business_has_no_hidden_domain_mapping() -> None:
    """验证未知业务名词不会被硬编码成一组猜测的实现符号。"""
    plan = build_semantic_query_plan("陨石收藏册如何归档？")

    assert plan["concepts"] == {}
    assert plan["code_terms"] == []


def test_annotation_marks_multi_concept_source_line() -> None:
    """验证源码行能标记钻石、展示和档位三个概念覆盖。"""
    plan = build_semantic_query_plan("前端展示钻石档位")
    hits = annotate_hits_with_concepts(
        [{"file": "php/DiamondControl.php", "line": 20, "content": "// 前端展示钻石档位"}],
        plan["concept_match_terms"],
    )

    assert {"display", "diamond", "gear"} <= set(hits[0]["matched_concepts"])


def test_preferred_repository_uses_high_ranked_source_without_excluding_others() -> None:
    """验证首轮命中只用于同仓加权，不会删除其他子仓的后续候选。"""
    repository = infer_preferred_repository(
        [
            {
                "file": "waky4_miyouphp/php/action/DiamondControl.php",
                "matched_queries": ["前端展示钻石", "展示钻石"],
            },
            {
                "file": "waky4_miyouadmin/pages/wallet.php",
                "matched_queries": ["展示"],
            },
        ]
    )

    assert repository == "waky4_miyouphp"


def test_concept_catalog_does_not_contain_php_implementation_tokens() -> None:
    """验证概念目录保持为业务词汇，不能退化成 PHP 类名或 SQL 表名清单。"""
    serialized = repr(SEMANTIC_CONCEPTS)

    assert "Control.php" not in serialized
    assert "Model.php" not in serialized
    assert "t_" not in serialized
