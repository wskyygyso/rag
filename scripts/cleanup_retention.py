"""RAgent 历史数据保留期限清理脚本。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

# 支持从项目根目录直接执行 `python scripts/cleanup_retention.py`。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retention import cleanup_retention
from app.settings import get_settings


def _positive_int(value: str) -> int:
    """解析正整数命令行参数。"""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("参数必须为正整数")
    return parsed


def parse_args() -> argparse.Namespace:
    """解析保留期限、批大小和显式应用模式。"""
    settings = get_settings()
    parser = argparse.ArgumentParser(description="清理超过保留期限的 RAgent 历史数据")
    parser.add_argument("--apply", action="store_true", help="执行删除；默认仅试运行")
    parser.add_argument(
        "--task-days", type=_positive_int, default=settings.diagnostic_task_retention_days
    )
    parser.add_argument(
        "--validation-days", type=_positive_int, default=settings.validation_audit_retention_days
    )
    parser.add_argument(
        "--security-days", type=_positive_int, default=settings.security_audit_retention_days
    )
    parser.add_argument(
        "--index-days", type=_positive_int, default=settings.document_index_retention_days
    )
    parser.add_argument("--batch-size", type=_positive_int, default=500)
    parser.add_argument("--max-batches", type=_positive_int, default=100)
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    """连接数据库执行清理，并以非零状态报告未在批次数内完成。"""
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        result = await cleanup_retention(
            pool,
            {
                "tasks": args.task_days,
                "validation_audit": args.validation_days,
                "security_audit": args.security_days,
                "document_index_runs": args.index_days,
            },
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            apply=args.apply,
        )
    finally:
        await pool.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 2


def main() -> None:
    """脚本入口，默认只做试运行统计。"""
    raise SystemExit(asyncio.run(main_async(parse_args())))


if __name__ == "__main__":
    main()
