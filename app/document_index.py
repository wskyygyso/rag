"""项目文档索引的运行时服务。

该模块负责计算仓库版本、复用完整索引、写入统一文档分块并维护索引运行状态。
它只读取挂载的待诊断项目，所有元数据写入 RAgent 自身数据库，避免修改目标代码。
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.document_chunks import build_repository_chunks, index_repository_documents
from app.embeddings import embedding_index_version, get_configured_embedding_provider
from app.settings import get_settings
from app.task_store import (
    cleanup_document_index_runs,
    create_pool,
    find_reusable_document_index_run,
    finish_document_index_run,
    recover_stale_document_index_runs,
    start_document_index_run_with_revision,
)

# 离线索引保持 `v1`；真实 embedding 会按模型和维度生成独立版本，防止旧哈希
# 向量或不同模型向量被错误复用。
INDEX_VERSION = "v1"


@dataclass(frozen=True)
class DocumentIndexResult:
    """描述一次文档索引的分块数量和是否复用既有结果。"""

    chunk_count: int
    reused: bool = False


def _repository_revision(root: str, project: str) -> Optional[str]:
    """读取目标项目顶层 Git 提交，供同版本索引复用判断使用。

    参数:
        - `root`：待诊断项目集合的挂载根目录，来自 CODE_REPOSITORIES_ROOT。
        - `project`：根目录下一级项目名，已经由接口层完成访问控制。

    返回:
        Git HEAD 提交 SHA；目录不是 Git 仓库、Git 不可用或读取失败时返回 None，
        此时仍执行全量分块同步，不能把缺少版本误判为索引失败。
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


async def run_document_index(project: str, dry_run: bool = False) -> DocumentIndexResult:
    """执行项目文档索引，或仅扫描并统计将写入的分块数量。

    参数:
        - `project`：需要建立索引的项目目录名，只允许读取仓库挂载根目录下的一级目录。
        - `dry_run`：为 True 时只扫描文件并返回统计结果，不连接或写入元数据库。

    返回:
        本次索引的分块数量和复用状态；提交版本相同且分块完整时直接复用旧结果。

    异常:
        目标目录越界、文件读取失败或数据库写入失败时向上抛出，调用方必须负责
        向用户返回失败原因；正式运行会在抛出前把索引记录标记为 FAILED。
    """
    settings = get_settings()
    if dry_run:
        return DocumentIndexResult(
            len(
                build_repository_chunks(
                    settings.code_repositories_root,
                    project,
                    embed=False,
                )
            )
        )
    embedding_provider = get_configured_embedding_provider(settings)
    index_version = embedding_index_version(embedding_provider)
    pool = await create_pool(settings)
    try:
        revision = _repository_revision(settings.code_repositories_root, project)
        await recover_stale_document_index_runs(pool, project)
        if revision:
            reusable = await find_reusable_document_index_run(
                pool, project, revision, index_version
            )
            if reusable is not None:
                return DocumentIndexResult(int(reusable["chunk_count"]), reused=True)
        run_id = await start_document_index_run_with_revision(
            pool, project, revision, index_version
        )
        count = await index_repository_documents(
            settings.code_repositories_root,
            project,
            pool,
            embedding_provider=embedding_provider,
        )
        await finish_document_index_run(pool, run_id, "COMPLETED", count)
        await cleanup_document_index_runs(pool, project)
        return DocumentIndexResult(count)
    except Exception as exc:
        if "run_id" in locals():
            await finish_document_index_run(pool, run_id, "FAILED", error=str(exc)[:2000])
        raise
    finally:
        await pool.close()


async def run_document_index_chunks(project: str, dry_run: bool = False) -> int:
    """执行项目文档索引并返回分块数量，供 Celery 和旧调用方使用。

    参数:
        - `project`：需要建立索引的项目目录名，数据来源为已通过访问控制的 API 请求。
        - `dry_run`：为 True 时只扫描并统计，不会写入 RAgent 元数据库。
    """
    result = await run_document_index(project, dry_run)
    return result.chunk_count


# 保留原脚本私有名称，避免已有运维脚本和测试在迁移期间立即失效。
_run_detailed = run_document_index
_run = run_document_index_chunks
