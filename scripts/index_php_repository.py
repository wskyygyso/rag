"""将指定项目的 PHP 符号索引写入 PostgreSQL。"""

import argparse
import asyncio
import sys
from pathlib import Path

# 支持从项目根目录直接执行 `python scripts/index_php_repository.py`。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.php_index import index_repository
from app.settings import get_settings
from app.task_store import create_pool, replace_symbol_index


async def _run(project: str, dry_run: bool = False) -> int:
    """扫描项目并写入符号表，或在 dry-run 模式下只返回扫描数量。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `dry_run`：是否仅扫描而不写入数据库，类型为 `bool`。
    """
    settings = get_settings()
    index = index_repository(settings.code_repositories_root, project)
    if dry_run:
        return len(index.get("symbols", []))
    pool = await create_pool(settings)
    try:
        return await replace_symbol_index(pool, project, index)
    finally:
        await pool.close()


def main() -> None:
    """解析命令行参数并执行 PHP 符号索引。"""
    parser = argparse.ArgumentParser(description="建立 PHP 项目符号索引")
    parser.add_argument("project", help="项目目录名称")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描并统计符号，不连接数据库或写入索引",
    )
    args = parser.parse_args()
    count = asyncio.run(_run(args.project, dry_run=args.dry_run))
    action = "扫描到" if args.dry_run else "已写入"
    print(f"{action} {count} 个符号")


if __name__ == "__main__":
    main()
