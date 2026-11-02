"""检查已记录 AI 评测的最新质量和趋势是否回归。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

# 支持从项目根目录直接执行回归检查。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_evaluation_runs import list_ai_evaluation_runs
from app.ai_regression import evaluate_ai_regression
from app.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析项目、历史数量和回归阈值参数。"""
    parser = argparse.ArgumentParser(description="检查 AI 固定评测回归")
    parser.add_argument("--project", required=True)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-evidence-recall", type=float, default=1.0)
    parser.add_argument("--max-pass-rate-drop", type=float, default=0.1)
    return parser.parse_args()


async def main_async() -> int:
    """查询最近两次评测并输出阈值检查结果。"""
    args = parse_args()
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        runs = await list_ai_evaluation_runs(pool, args.project, 2)
    finally:
        await pool.close()
    result = evaluate_ai_regression(
        runs,
        min_pass_rate=args.min_pass_rate,
        min_evidence_recall=args.min_evidence_recall,
        max_pass_rate_drop=args.max_pass_rate_drop,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def main() -> None:
    """脚本入口。"""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
