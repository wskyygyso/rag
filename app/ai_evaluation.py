"""AI 诊断叙写的证据引用和回答质量评测。"""

from typing import Any, Dict, Iterable, Set


def _recall(expected: Iterable[str], actual: Set[str]) -> float:
    """计算证据引用召回率，空期望视为完全命中。"""
    expected_set = {str(item) for item in expected}
    if not expected_set:
        return 1.0
    return len(expected_set & actual) / len(expected_set)


def evaluate_ai_case(case: Dict[str, Any], diagnosis: Dict[str, Any]) -> Dict[str, Any]:
    """按证据引用、不确定性、建议动作和原因数量评估单个 AI 输出。"""
    causes = diagnosis.get("possible_causes") or []
    actual_refs = {
        str(ref)
        for cause in causes
        for ref in cause.get("evidence_refs") or []
    }
    expected_refs = {
        f"{item['file']}:{item['line']}"
        for item in case.get("expected_lines") or []
    }
    rules = case.get("ai") or {}
    max_causes = int(rules.get("max_causes", 5))
    require_uncertainty = bool(rules.get("require_uncertainty", True))
    require_actions = bool(rules.get("require_actions", True))

    checks = {
        "conclusion_present": bool(str(diagnosis.get("conclusion") or "").strip()),
        "evidence_refs_complete": _recall(expected_refs, actual_refs) == 1.0,
        "cause_limit": len(causes) <= max_causes,
        "uncertainty_present": bool(diagnosis.get("uncertainties")) if require_uncertainty else True,
        "actions_present": bool(diagnosis.get("recommended_actions")) if require_actions else True,
    }
    return {
        "id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "expected_refs": sorted(expected_refs),
        "actual_refs": sorted(actual_refs),
        "evidence_ref_recall": _recall(expected_refs, actual_refs),
        "cause_count": len(causes),
    }


def summarize_ai_evaluations(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总 AI 用例通过率和平均证据引用召回。"""
    items = list(results)
    passed = sum(1 for item in items if item["passed"])
    average_recall = (
        sum(float(item["evidence_ref_recall"]) for item in items) / len(items)
        if items
        else 0.0
    )
    return {
        "total": len(items),
        "passed": passed,
        "pass_rate": passed / len(items) if items else 0.0,
        "average_evidence_ref_recall": average_recall,
        "cases": items,
    }
