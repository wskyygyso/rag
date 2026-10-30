"""AI 诊断质量评测逻辑测试。"""

from app.ai_evaluation import evaluate_ai_case, summarize_ai_evaluations


def test_ai_evaluation_accepts_grounded_answer() -> None:
    """验证完整证据引用、不确定性和建议动作通过。"""
    case = {
        "id": "case",
        "expected_lines": [{"file": "Guild.php", "line": 8}],
        "ai": {"max_causes": 2},
    }
    diagnosis = {
        "conclusion": "结论",
        "possible_causes": [
            {"title": "x", "reasoning": "y", "evidence_refs": ["Guild.php:8"]}
        ],
        "uncertainties": ["未验证运行时数据"],
        "recommended_actions": ["只读核对配置"],
    }
    result = evaluate_ai_case(case, diagnosis)
    assert result["passed"] is True
    assert result["evidence_ref_recall"] == 1.0


def test_ai_evaluation_rejects_missing_reference_and_uncertainty() -> None:
    """验证缺少证据引用或不确定说明时失败。"""
    case = {
        "id": "case",
        "expected_lines": [{"file": "Guild.php", "line": 8}],
        "ai": {"max_causes": 1, "require_uncertainty": True},
    }
    result = evaluate_ai_case(
        case,
        {
            "conclusion": "结论",
            "possible_causes": [
                {"title": "x", "reasoning": "y", "evidence_refs": ["Guild.php:7"]},
                {"title": "z", "reasoning": "w", "evidence_refs": ["Guild.php:9"]},
            ],
            "uncertainties": [],
            "recommended_actions": ["动作"],
        },
    )
    assert result["passed"] is False
    assert result["checks"]["evidence_refs_complete"] is False
    assert result["checks"]["uncertainty_present"] is False
    assert result["checks"]["cause_limit"] is False


def test_ai_evaluation_summarizes_pass_rate_and_recall() -> None:
    """验证整体通过率和平均证据引用召回。"""
    summary = summarize_ai_evaluations(
        [
            {"passed": True, "evidence_ref_recall": 1.0},
            {"passed": False, "evidence_ref_recall": 0.5},
        ]
    )
    assert summary["pass_rate"] == 0.5
    assert summary["average_evidence_ref_recall"] == 0.75
