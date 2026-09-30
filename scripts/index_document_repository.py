"""将项目代码、配置和文档写入统一分块索引。"""

import argparse
import asyncio
import sys
from pathlib import Path

# 支持从项目根目录直接执行脚本。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document_chunks import build_repository_chunks, index_repository_documents
from app.settings import get_settings
from app.task_store import create_pool, finish_document_index_run, start_document_index_run


async def _run(project: str, dry_run: bool = False) -> int:
    """扫描项目并写入分块索引，dry-run 只统计分块数量。"""
    settings = get_settings()
    if dry_run:
        return len(build_repository_chunks(settings.code_repositories_root, project))
    pool = await create_pool(settings)
    run_id = await start_document_index_run(pool, project)
    try:
        count = await index_repository_documents(settings.code_repositories_root, project, pool)
        await finish_document_index_run(pool, run_id, "COMPLETED", count)
        return count
    except Exception as exc:
        await finish_document_index_run(pool, run_id, "FAILED", error=str(exc)[:2000])
        raise
    finally:
        await pool.close()


def main() -> None:
    """解析项目参数并执行文档分块索引。"""
    parser = argparse.ArgumentParser(description="建立项目统一文档分块索引")
    parser.add_argument("project", help="项目目录名称")
    parser.add_argument("--dry-run", action="store_true", help="只扫描并统计分块，不连接数据库")
    args = parser.parse_args()
    count = asyncio.run(_run(args.project, dry_run=args.dry_run))
    print(f"{'扫描到' if args.dry_run else '已写入'} {count} 个文档分块")


if __name__ == "__main__":
    main()
