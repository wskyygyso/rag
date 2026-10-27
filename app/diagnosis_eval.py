"""基于固定仓库和问题集的离线诊断准确性评测。"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

from app.code_tools import extract_search_queries, search_code
from app.php_index import index_repository


def load_cases(path: str) -> List[Dict[str, Any]]:
    """加载并校验 JSON 诊断评测集，要求 case ID 唯一。"""
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = content.get("cases") if isinstance(content, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测集必须包含非空 cases 数组")
    seen_ids: Set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("每个评测用例必须是对象")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("评测用例必须包含非空 id")
        if case_id in seen_ids:
            raise ValueError(f"评测用例 id 重复：{case_id}")
        if not case.get("project") or not case.get("question"):
            raise ValueError(f"评测用例 {case_id} 缺少 project 或 question")
        seen_ids.add(case_id)
    return cases


def _recall(expected: Iterable[str], actual: Set[str]) -> float:
    """计算集合召回率，空期望视为完全命中。"""
    expected_set = {str(item) for item in expected}
    if not expected_set:
        return 1.0
    return len(expected_set & actual) / len(expected_set)


def _evaluate_case(root: str, case: Dict[str, Any], index: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个用例并返回命中检查、召回率和通过状态。"""
    queries = extract_search_queries(case["question"])
    evidence: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int, str]] = set()
    for query in queries:
        result = search_code(root, case["project"], query, max_results=20)
        for match in result["matches"]:
            key = (match["file"], int(match["line"]), match["content"])
            if key not in seen:
                seen.add(key)
                evidence.append(match)

    actual_symbols = {str(item["symbol_name"]) for item in index.get("symbols", [])}
    actual_files = {str(item["file"]) for item in evidence}
    evidence_lines = {(str(item["file"]), int(item["line"])) for item in evidence}
    expected_symbols = [str(item) for item in case.get("expected_symbols", [])]
    for symbol in index.get("symbols", []):
        if symbol.get("symbol_name") in expected_symbols:
            symbol_file = str(symbol["file"])
            symbol_line = int(symbol["line"])
            actual_files.add(symbol_file)
            evidence_lines.add((symbol_file, symbol_line))
    query_set = {query.lower() for query in queries}
    expected_files = [str(item) for item in case.get("expected_files", [])]
    expected_queries = [str(item).lower() for item in case.get("expected_queries", [])]

    symbol_recall = _recall(expected_symbols, actual_symbols)
    file_recall = _recall(expected_files, actual_files)
    query_recall = _recall(expected_queries, query_set)
    expected_lines = {
        (str(item["file"]), int(item["line"]))
        for item in case.get("expected_lines", [])
    }
    line_recall = _recall(
        (f"{file}:{line}" for file, line in expected_lines),
        {f"{file}:{line}" for file, line in evidence_lines},
    )
    passed = symbol_recall == 1.0 and file_recall == 1.0 and query_recall == 1.0 and line_recall == 1.0
    return {
        "id": case["id"],
        "passed": passed,
        "queries": queries,
        "evidence_count": len(evidence),
        "recall": {
            "symbols": symbol_recall,
            "files": file_recall,
            "queries": query_recall,
            "lines": line_recall,
        },
    }


def evaluate_cases(root: str, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按项目复用符号索引并汇总整体通过率。"""
    indexes: Dict[str, Dict[str, Any]] = {}
    results = []
    for case in cases:
        project = case["project"]
        if project not in indexes:
            indexes[project] = index_repository(root, project)
        results.append(_evaluate_case(root, case, indexes[project]))
    passed = sum(1 for result in results if result["passed"])
    return {
        "total": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "cases": results,
    }
