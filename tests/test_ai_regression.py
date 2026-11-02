"""AI 评测回归门禁测试。"""

import pytest

from app.ai_regression import evaluate_ai_regression


def test_ai_regression_accepts_stable_passing_runs() -> None:
    """验证稳定高通过率运行通过门禁。

    参数:
        - 无：测试构造固定两条运行记录。
    """
    result = evaluate_ai_regression(
        [
            {"pass_rate": 1.0, "average_evidence_ref_recall": 1.0},
            {"pass_rate": 0.9, "average_evidence_ref_recall": 0.9},
        ]
    )
    assert result["passed"] is True
    assert result["pass_rate_delta"] == 0.1


def test_ai_regression_rejects_pass_rate_drop() -> None:
    """验证通过率相对上次下降过多时失败。

    参数:
        - 无：测试构造固定下降趋势。
    """
    result = evaluate_ai_regression(
        [
            {"pass_rate": 0.7, "average_evidence_ref_recall": 1.0},
            {"pass_rate": 1.0, "average_evidence_ref_recall": 1.0},
        ],
        max_pass_rate_drop=0.1,
    )
    assert result["passed"] is False
    assert result["checks"]["trend"] is False


def test_ai_regression_fails_without_history() -> None:
    """验证没有评测记录时不会空数据误判通过。

    参数:
        - 无：测试传入空运行集合。
    """
    result = evaluate_ai_regression([])
    assert result["passed"] is False


def test_ai_regression_rejects_invalid_thresholds() -> None:
    """验证非法阈值被拒绝。

    参数:
        - 无：测试传入超出范围的门槛。
    """
    with pytest.raises(ValueError):
        evaluate_ai_regression(
            [{"pass_rate": 1.0, "average_evidence_ref_recall": 1.0}],
            min_pass_rate=2,
        )
