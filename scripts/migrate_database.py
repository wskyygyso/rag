"""RAgent PostgreSQL 迁移、检查和发布前漂移门禁命令行。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

# 支持从项目根目录直接执行 Python 脚本。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.migrations import MigrationError, apply_migrations, inspect_migrations
from app.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析迁移目录、数据库地址和执行模式。"""
    parser = argparse.ArgumentParser(description="RAgent 数据库迁移器")
    parser.add_argument(
        "--migrations-dir",
        default=str(Path(__file__).resolve().parents[1] / "migrations"),
        help="迁移 SQL 目录",
    )
    parser.add_argument("--database-url", help="覆盖 DATABASE_URL，仅支持 PostgreSQL")
    parser.add_argument("--check", action="store_true", help="只检查待执行或漂移迁移")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    """连接元数据库并执行检查或迁移，失败时返回非零状态。

    参数:
        - `args`：命令行解析结果，包含迁移目录、数据库地址和检查模式。
    """
    database_url = args.database_url or get_settings().database_url
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    try:
        if args.check:
            result = await inspect_migrations(pool, args.migrations_dir)
            passed = not result["pending"] and not result["drifted"]
            result["passed"] = passed
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"pending={len(result['pending'])} drifted={len(result['drifted'])}")
                for version in result["pending"]:
                    print(f"PENDING {version}")
                for version in result["drifted"]:
                    print(f"DRIFTED {version}")
            return 0 if passed else 1
        applied = await apply_migrations(pool, args.migrations_dir)
        if args.json:
            print(json.dumps({"applied": applied}, ensure_ascii=False, indent=2))
        else:
            print(f"applied={len(applied)}")
            for version in applied:
                print(f"APPLIED {version}")
        return 0
    except MigrationError as exc:
        print(f"迁移门禁失败：{exc}", file=sys.stderr)
        return 2
    finally:
        await pool.close()


def main() -> None:
    """脚本入口。"""
    raise SystemExit(asyncio.run(main_async(parse_args())))


if __name__ == "__main__":
    main()
