"""导出历史正确反馈对应的待审核诊断评测候选。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

# 支持从项目根目录直接执行历史案例导出。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation_cases import fetch_candidates
from app.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析项目、时间范围、数量和输出路径。"""
    parser = argparse.ArgumentParser(description="导出待审核诊断评测候选")
    parser.add_argument("--project")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", help="输出 JSON 文件；不指定时打印到标准输出")
    parser.add_argument("--dry-run", action="store_true", help="只输出候选数量，不输出明细")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    """连接元数据库导出候选，并拒绝覆盖已有文件。

    参数:
        - `args`：可变位置参数集合，类型为 `argparse.Namespace`。
    """
    if args.days < 1 or args.days > 3650 or args.limit < 1 or args.limit > 1000:
        raise SystemExit("--days 或 --limit 超出允许范围")
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        candidates = await fetch_candidates(
            pool,
            project=args.project,
            days=args.days,
            limit=args.limit,
        )
    finally:
        await pool.close()

    if args.dry_run:
        print(json.dumps({"candidates": len(candidates), "review_status": "pending"}, ensure_ascii=False))
        return 0
    content = json.dumps({"cases": candidates}, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with open(args.output, "x", encoding="utf-8") as output:
            output.write(content)
        print(f"exported={args.output} candidates={len(candidates)}")
    else:
        print(content, end="")
    return 0


def main() -> None:
    """脚本入口。"""
    raise SystemExit(asyncio.run(main_async(parse_args())))


if __name__ == "__main__":
    main()
