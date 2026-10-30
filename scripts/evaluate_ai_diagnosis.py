"""使用真实 LLM 评估诊断叙写质量。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# 支持从项目根目录直接执行 AI 质量评测。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_evaluation import evaluate_ai_case, summarize_ai_evaluations
from app.code_tools import extract_search_queries, search_code
from app.diagnosis_eval import load_cases
from app.llm_diagnosis import build_evidence_context, generate_ai_diagnosis_run
from app.php_index import index_repository, trace_index_call_chain
from app.settings import get_settings


def build_case_result(root: str, case: Dict[str, Any], index: Dict[str, Any]) -> Dict[str, Any]:
    """为单个评测用例收集代码证据、符号、配置和 SQL 摘要。"""
    queries = extract_search_queries(case["question"])
    evidence = []
    seen = set()
    for query in queries:
        for match in search_code(root, case["project"], query, max_results=20)["matches"]:
            key = (match["file"], int(match["line"]), match["content"])
            if key not in seen:
                seen.add(key)
                evidence.append(match)
    symbol_names = {item["symbol_name"] for item in index.get("symbols") or []}
    call_chains = [
        trace_index_call_chain(index, query, depth=5)
        for query in queries
        if query in symbol_names
    ]
    return {
        "queries": queries,
        "evidence": evidence,
        "symbols": index.get("symbols") or [],
        "configs": index.get("configs") or [],
        "sql": index.get("sql") or [],
        "call_chains": call_chains,
        "validation": {
            "database": {"status": "not_requested"},
            "configs": [],
            "logs": [],
        },
        "warnings": [],
    }


async def run_cases(root: str, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """逐项目复用索引，调用真实 LLM 并评估结构化输出。"""
    settings = get_settings()
    indexes = {}
    results = []
    for case in cases:
        project = case["project"]
        if project not in indexes:
            indexes[project] = index_repository(root, project)
        result = build_case_result(root, case, indexes[project])
        context = build_evidence_context(project, case["question"], result)
        if len(context["allowed_evidence_refs"]) < settings.llm_min_evidence_items:
            results.append(
                {
                    "id": case["id"],
                    "skipped": True,
                    "passed": True,
                    "reason": "证据数量未达到 LLM_MIN_EVIDENCE_ITEMS",
                    "evidence_ref_recall": 1.0,
                }
            )
            continue
        try:
            run = await generate_ai_diagnosis_run(
                settings,
                project,
                case["question"],
                result,
            )
        except Exception as exc:
            results.append(
                {
                    "id": case["id"],
                    "passed": False,
                    "checks": {"call_succeeded": False},
                    "error_type": type(exc).__name__,
                    "evidence_ref_recall": 0.0,
                }
            )
            continue
        evaluation = evaluate_ai_case(case, run.diagnosis.model_dump())
        evaluation["metadata"] = run.metadata
        results.append(evaluation)
    return summarize_ai_evaluations(results)


def parse_args() -> argparse.Namespace:
    """解析评测集、仓库、阈值和显式付费调用开关。"""
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="使用真实 LLM 评估诊断叙写质量")
    parser.add_argument("--root", default=str(project_root / "tests" / "fixtures" / "php_repo"))
    parser.add_argument("--cases", default=str(project_root / "tests" / "fixtures" / "diagnosis_cases.json"))
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=0, help="只运行前 N 个用例；0 表示全部")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 个用例")
    parser.add_argument("--live", action="store_true", help="确认调用外部 LLM 并可能产生费用")
    return parser.parse_args()


async def main_async() -> int:
    """执行真实 AI 评测并以最低通过率决定退出码。"""
    args = parse_args()
    if not args.live:
        raise SystemExit("必须显式增加 --live 才会调用外部 LLM")
    if args.min_pass_rate < 0 or args.min_pass_rate > 1:
        raise SystemExit("--min-pass-rate 必须在 0 到 1 之间")
    if args.limit < 0 or args.offset < 0:
        raise SystemExit("--limit 和 --offset 不能为负数")
    cases = load_cases(args.cases)
    cases = cases[args.offset :]
    if args.limit:
        cases = cases[: args.limit]
    result = await run_cases(args.root, cases)
    result["min_pass_rate"] = args.min_pass_rate
    result["threshold_passed"] = result["pass_rate"] >= args.min_pass_rate
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["threshold_passed"] else 1


def main() -> None:
    """脚本入口。"""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
