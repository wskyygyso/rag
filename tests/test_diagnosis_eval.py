"""离线诊断准确性评测测试。"""

from pathlib import Path

import pytest

from app.diagnosis_eval import evaluate_cases, load_cases


def test_fixed_diagnosis_cases_pass_baseline() -> None:
    """验证固定样例集的符号、文件、关键词和行号全部命中。"""
    root = Path(__file__).parent / "fixtures" / "php_repo"
    cases = load_cases(str(Path(__file__).parent / "fixtures" / "diagnosis_cases.json"))
    result = evaluate_cases(str(root), cases)
    assert result["total"] == 3
    assert result["passed"] == 3
    assert result["pass_rate"] == 1.0


def test_diagnosis_eval_reports_missing_symbol() -> None:
    """验证期望符号不存在时用例失败而不是误报通过。"""
    root = Path(__file__).parent / "fixtures" / "php_repo"
    result = evaluate_cases(
        str(root),
        [
            {
                "id": "missing_symbol",
                "project": "waky3",
                "question": "GuildController::inviteGuild",
                "expected_symbols": ["GuildController::missingMethod"],
            }
        ],
    )
    assert result["passed"] == 0
    assert result["cases"][0]["recall"]["symbols"] == 0.0


def test_diagnosis_eval_rejects_duplicate_case_ids(tmp_path) -> None:
    """验证重复用例 ID 会被拒绝，避免统计混淆。"""
    path = tmp_path / "cases.json"
    path.write_text(
        '{"cases":[{"id":"same","project":"waky3","question":"a"},{"id":"same","project":"waky3","question":"b"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复"):
        load_cases(str(path))
