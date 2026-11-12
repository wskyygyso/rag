"""跨诊断任务复用项目符号索引的 Redis 缓存。"""

import asyncio
import gzip
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from redis import asyncio as redis_asyncio

from app.code_tools import CodeRepository
from app.php_index import index_repository, iter_php_files

logger = logging.getLogger(__name__)

# 缓存版本随索引结构变化。v2 增加中文注释术语分组 glossary；旧版本缓存虽然
# 仍可解码，但缺少术语会导致知识库为空，因此升级前缀强制重建一次。
_INDEX_KEY_PREFIX = "ragent:project-symbol-index:v2"
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class ProjectIndexResult:
    """描述一次项目符号索引准备结果及可审计的性能元数据。

    字段:
        - `index`：可供检索规划、调用链和证据摘要复用的完整 PHP 符号索引。
        - `fingerprint`：由项目内 PHP 文件路径、大小和纳秒修改时间计算的版本指纹。
        - `cache_status`：`redis_hit`、`built`、`redis_hit_after_wait`、
          `bypass` 或 `redis_error`，说明索引来自缓存、单飞构建还是降级路径。
        - `cache_wait_seconds`：等待同项目其他任务完成索引构建的实际秒数。
        - `build_seconds`：当前任务真正执行仓库扫描所耗秒数；命中缓存时为零。
        - `cache_key`：本次版本对应的 Redis 缓存键，便于审计缓存是否被正确复用。
    """

    index: Dict[str, list]
    fingerprint: str
    cache_status: str
    cache_wait_seconds: float
    build_seconds: float
    cache_key: str

    def audit_dict(self) -> Dict[str, Any]:
        """返回适合写入任务结果的安全审计字段，不暴露 Redis 凭据。"""
        return {
            "fingerprint": self.fingerprint,
            "cache_status": self.cache_status,
            "cache_wait_ms": round(self.cache_wait_seconds * 1000, 3),
            "build_ms": round(self.build_seconds * 1000, 3),
            "cache_key": self.cache_key,
        }


def repository_fingerprint(root: str, project: str) -> str:
    """计算 PHP 项目当前文件快照的稳定指纹。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：当前仓库标识，类型为 `str`；只允许解析为根目录的直接子目录。

    Returns:
        SHA-256 十六进制指纹。指纹覆盖每个 PHP 文件的相对路径、字节数和纳秒
        修改时间，避免代码变更后继续复用旧索引；不读取文件正文，控制大型仓库
        的重复扫描成本。
    """
    repository = CodeRepository(root, project)
    digest = hashlib.sha256()
    paths = sorted(
        iter_php_files(repository),
        key=lambda path: path.relative_to(repository.path).as_posix(),
    )
    for path in paths:
        stat = path.stat()
        relative_path = path.relative_to(repository.path).as_posix()
        digest.update(
            f"{relative_path}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8")
        )
    return digest.hexdigest()


def encode_index(index: Dict[str, list]) -> bytes:
    """把符号索引压缩为可安全写入 Redis 的二进制载荷。

    参数:
        - `index`：`index_repository` 生成的符号、引用、配置和 SQL 集合。

    Returns:
        gzip 压缩后的 JSON 字节。压缩可显著降低数万符号在 Redis 与 Worker
        之间传输的带宽，并保持首次解压后结构与原索引完全一致。
    """
    serialized = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(serialized, compresslevel=6)


def decode_index(payload: Any) -> Dict[str, list]:
    """解码缓存载荷并校验四类索引集合。

    参数:
        - `payload`：Redis 返回的压缩 JSON，兼容 `bytes` 和测试中的 `str`。

    Returns:
        可直接供检索链路使用的项目索引；载荷损坏或结构缺失时抛出
        `ValueError`，调用方会删除坏缓存并重新构建。`glossary` 是后加的派生
        知识字段，旧版本缓存缺少该字段时按空列表处理，不影响既有索引复用。
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, bytes):
        raise ValueError("项目符号索引缓存不是二进制载荷")
    decoded = json.loads(gzip.decompress(payload).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("项目符号索引缓存根结构不是对象")
    result: Dict[str, list] = {}
    for key in ("symbols", "references", "configs", "sql"):
        value = decoded.get(key)
        if not isinstance(value, list):
            raise ValueError(f"项目符号索引缓存缺少列表字段：{key}")
        result[key] = value
    # 只有缓存中真实存在列表型 glossary 时才补齐字段；测试和旧缓存可能没有
    # 该键，保持原样可避免把“缺失字段”误判为知识库为空并破坏索引结构比较。
    glossary = decoded.get("glossary")
    if isinstance(glossary, list):
        result["glossary"] = glossary
    return result


def project_index_cache_key(project: str, fingerprint: str) -> str:
    """生成不包含仓库绝对路径的版本化缓存键。

    参数:
        - `project`：当前项目标识，类型为 `str`。
        - `fingerprint`：`repository_fingerprint` 计算出的代码快照版本。

    Returns:
        Redis 键。项目名经过摘要处理，避免特殊字符影响键空间，同时保留前缀便于
        运维按命名空间清理。
    """
    project_digest = hashlib.sha256(project.encode("utf-8")).hexdigest()[:16]
    return f"{_INDEX_KEY_PREFIX}:{project_digest}:{fingerprint}"


async def _release_lock(client: Any, lock_key: str, token: str) -> None:
    """仅在令牌仍属于当前 Worker 时释放项目索引构建锁。

    参数:
        - `client`：兼容 redis.asyncio `eval` 接口的客户端。
        - `lock_key`：项目索引单飞锁键，类型为 `str`。
        - `token`：本次构建持有的随机令牌，类型为 `str`。
    """
    try:
        await client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)
    except Exception as exc:
        logger.warning(
            "project_index_lock_release_failed error_type=%s",
            type(exc).__name__,
        )


async def _build_index(
    root: str,
    project: str,
    index_builder: Callable[[str, str], Dict[str, list]],
) -> tuple[Dict[str, list], float]:
    """在线程池中扫描仓库，避免阻塞 Celery 进程的事件循环。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：当前项目标识，类型为 `str`。
        - `index_builder`：索引构建函数，生产环境默认为 `index_repository`。

    Returns:
        `(索引, 扫描秒数)`。扫描异常原样抛给诊断任务，由任务状态记录明确失败，
        不缓存半份索引。
    """
    started_at = time.perf_counter()
    index = await asyncio.to_thread(index_builder, root, project)
    return index, time.perf_counter() - started_at


async def load_project_symbol_index(
    *,
    root: str,
    project: str,
    redis_url: str,
    cache_enabled: bool = True,
    cache_ttl_seconds: int = 604800,
    lock_ttl_seconds: int = 300,
    wait_timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 0.2,
    redis_client: Any = None,
    index_builder: Callable[[str, str], Dict[str, list]] = index_repository,
    fingerprint: Optional[str] = None,
) -> ProjectIndexResult:
    """读取或单飞构建当前项目的完整符号索引。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：当前项目标识，类型为 `str`。
        - `redis_url`：RAgent 共享 Redis 地址，类型为 `str`。
        - `cache_enabled`：是否启用跨 Worker 复用；关闭时保持每次扫描的降级行为。
        - `cache_ttl_seconds`：版本化索引最长保留秒数，类型为 `int`。
        - `lock_ttl_seconds`：构建锁最长持有秒数；必须覆盖大型仓库扫描耗时。
        - `wait_timeout_seconds`：缓存竞争者等待首个构建的最长秒数。
        - `poll_interval_seconds`：等待期间检查缓存和锁的间隔秒数。
        - `redis_client`：可注入的异步 Redis 客户端，主要用于测试和复用连接。
        - `index_builder`：索引构建函数，必须返回 symbols、references、configs 和 sql。
        - `fingerprint`：可预先提供的代码快照指纹，主要用于测试；为空时实时计算。

    Returns:
        带缓存来源、等待时间和构建耗时的 `ProjectIndexResult`。Redis 不可用或
        缓存损坏时回退到真实仓库扫描，保证诊断能力不因缓存故障中断。
    """
    fingerprint = fingerprint or await asyncio.to_thread(repository_fingerprint, root, project)
    cache_key = project_index_cache_key(project, fingerprint)
    if not cache_enabled:
        index, build_seconds = await _build_index(root, project, index_builder)
        return ProjectIndexResult(index, fingerprint, "bypass", 0.0, build_seconds, cache_key)

    owns_client = redis_client is None
    client = redis_client or redis_asyncio.from_url(redis_url)
    try:
        try:
            payload = await client.get(cache_key)
            if payload is not None:
                return ProjectIndexResult(
                    decode_index(payload), fingerprint, "redis_hit", 0.0, 0.0, cache_key
                )
        except (ValueError, OSError):
            await client.delete(cache_key)

        lock_key = f"{cache_key}:lock"
        token = uuid.uuid4().hex
        acquired = bool(
            await client.set(lock_key, token, nx=True, ex=max(1, lock_ttl_seconds))
        )
        wait_started_at = time.perf_counter()
        had_to_wait = not acquired
        if not acquired:
            deadline = wait_started_at + max(0.0, wait_timeout_seconds)
            while time.perf_counter() < deadline:
                await asyncio.sleep(max(0.01, poll_interval_seconds))
                payload = await client.get(cache_key)
                if payload is not None:
                    try:
                        return ProjectIndexResult(
                            decode_index(payload),
                            fingerprint,
                            "redis_hit_after_wait",
                            time.perf_counter() - wait_started_at,
                            0.0,
                            cache_key,
                        )
                    except (ValueError, OSError):
                        await client.delete(cache_key)
                acquired = bool(
                    await client.set(
                        lock_key,
                        token,
                        nx=True,
                        ex=max(1, lock_ttl_seconds),
                    )
                )
                if acquired:
                    break
            if not acquired:
                index, build_seconds = await _build_index(root, project, index_builder)
                return ProjectIndexResult(
                    index,
                    fingerprint,
                    "redis_error",
                    time.perf_counter() - wait_started_at,
                    build_seconds,
                    cache_key,
                )

        try:
            # 获得锁后再次读取，防止等待期间前一个构建者刚写完缓存后再次扫描。
            payload = await client.get(cache_key)
            if payload is not None:
                return ProjectIndexResult(
                    decode_index(payload),
                    fingerprint,
                    "redis_hit_after_wait",
                    time.perf_counter() - wait_started_at,
                    0.0,
                    cache_key,
                )
            index, build_seconds = await _build_index(root, project, index_builder)
            await client.set(cache_key, encode_index(index), ex=max(1, cache_ttl_seconds))
            waited_seconds = (
                time.perf_counter() - wait_started_at if had_to_wait else 0.0
            )
            return ProjectIndexResult(
                index,
                fingerprint,
                "built",
                waited_seconds,
                build_seconds,
                cache_key,
            )
        finally:
            await _release_lock(client, lock_key, token)
    except Exception as exc:
        logger.warning(
            "project_index_cache_unavailable project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        index, build_seconds = await _build_index(root, project, index_builder)
        return ProjectIndexResult(
            index,
            fingerprint,
            "redis_error",
            0.0,
            build_seconds,
            cache_key,
        )
    finally:
        if owns_client:
            await client.aclose()
