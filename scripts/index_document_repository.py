"""将项目代码、配置和文档写入统一分块索引。"""

import argparse
import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 支持从项目根目录直接执行脚本。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document_chunks import build_repository_chunks, index_repository_documents
from app.settings import get_settings
from app.task_store import (
    create_pool,
    cleanup_document_index_runs,
    finish_document_index_run,
    find_reusable_document_index_run,
    recover_stale_document_index_runs,
    start_document_index_run_with_revision,
)

INDEX_VERSION = "v1"


@dataclass(frozen=True)
class DocumentIndexResult:
    """表示一次文档索引统计结果和是否复用已有索引。"""

    chunk_count: int
    reused: bool = False


def _repository_revision(root: str, project: str) -> Optional[str]:
    """读取项目 Git HEAD，非 Git 仓库或命令失败时返回 None。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
    """
    try:
        return subprocess.run(
            ["git", "-C", str(Path(root) / project), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


async def _run_detailed(project: str, dry_run: bool = False) -> DocumentIndexResult:
    """执行索引或复用同版本索引，并返回分块数量和复用状态。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `dry_run`：是否仅扫描而不写入数据库，类型为 `bool`。
    """
    settings = get_settings()
    if dry_run:
        return DocumentIndexResult(
            len(build_repository_chunks(settings.code_repositories_root, project))
        )
    pool = await create_pool(settings)
    try:
        revision = _repository_revision(settings.code_repositories_root, project)
        await recover_stale_document_index_runs(pool, project)
        if revision:
            reusable = await find_reusable_document_index_run(
                pool, project, revision, INDEX_VERSION
            )
            if reusable is not None:
                return DocumentIndexResult(int(reusable["chunk_count"]), reused=True)
        run_id = await start_document_index_run_with_revision(
            pool, project, revision, INDEX_VERSION
        )
        count = await index_repository_documents(settings.code_repositories_root, project, pool)
        await finish_document_index_run(pool, run_id, "COMPLETED", count)
        await cleanup_document_index_runs(pool, project)
        return DocumentIndexResult(count)
    except Exception as exc:
        if "run_id" in locals():
            await finish_document_index_run(pool, run_id, "FAILED", error=str(exc)[:2000])
        raise
    finally:
        await pool.close()


async def _run(project: str, dry_run: bool = False) -> int:
    """兼容旧调用方，仅返回文档分块数量。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `dry_run`：是否仅扫描而不写入数据库，类型为 `bool`。
    """
    result = await _run_detailed(project, dry_run)
    return result.chunk_count


def main() -> None:
    """解析项目参数并执行文档分块索引。"""
    parser = argparse.ArgumentParser(description="建立项目统一文档分块索引")
    parser.add_argument("project", help="项目目录名称")
    parser.add_argument("--dry-run", action="store_true", help="只扫描并统计分块，不连接数据库")
    args = parser.parse_args()
    result = asyncio.run(_run_detailed(args.project, dry_run=args.dry_run))
    action = "扫描到" if args.dry_run else "复用" if result.reused else "已写入"
    print(f"{action} {result.chunk_count} 个文档分块")


if __name__ == "__main__":
    main()
