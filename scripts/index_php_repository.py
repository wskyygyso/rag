"""将指定项目的 PHP 符号索引写入 PostgreSQL。"""

import argparse
import asyncio

from app.php_index import index_repository
from app.settings import get_settings
from app.task_store import create_pool, replace_symbol_index


async def _run(project: str) -> int:
    """扫描项目、写入符号表并返回写入数量。"""
    settings = get_settings()
    index = index_repository(settings.code_repositories_root, project)
    pool = await create_pool(settings)
    try:
        return await replace_symbol_index(pool, project, index)
    finally:
        await pool.close()


def main() -> None:
    """解析命令行参数并执行 PHP 符号索引。"""
    parser = argparse.ArgumentParser(description="建立 PHP 项目符号索引")
    parser.add_argument("project", help="项目目录名称")
    args = parser.parse_args()
    count = asyncio.run(_run(args.project))
    print(f"已写入 {count} 个符号")


if __name__ == "__main__":
    main()
