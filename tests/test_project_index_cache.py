"""验证项目符号索引缓存复用、单飞构建和代码快照失效。"""

import asyncio
import time
from pathlib import Path

from app.project_index_cache import (
    decode_index,
    encode_index,
    load_project_symbol_index,
    repository_fingerprint,
)


class _Redis:
    """模拟项目索引缓存使用的异步 Redis 原语。"""

    def __init__(self):
        """初始化独立键空间和调用计数。"""
        self.values = {}
        self.get_calls = 0
        self.set_calls = 0
        self.closed = False

    async def get(self, key):
        """读取测试键。

        参数:
            - `key`：索引缓存或构建锁键，类型为 `str`。
        """
        self.get_calls += 1
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        """按 Redis `SET NX EX` 语义写入测试值。

        参数:
            - `key`：待写入键，类型为 `str`。
            - `value`：缓存载荷或锁令牌。
            - `nx`：为真时仅允许键不存在时写入。
            - `ex`：测试替身忽略的过期秒数。
        """
        self.set_calls += 1
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        """删除无效缓存或释放测试锁。

        参数:
            - `key`：待删除键，类型为 `str`。
        """
        self.values.pop(key, None)
        return 1

    async def eval(self, script, key_count, key, token):
        """模拟令牌匹配后安全释放锁的 Lua 脚本。

        参数:
            - `script`：生产使用的释放锁脚本；测试只验证键和令牌语义。
            - `key_count`：脚本访问的 Redis 键数量。
            - `key`：项目索引锁键，类型为 `str`。
            - `token`：当前构建者令牌，类型为 `str`。
        """
        if key_count == 1 and self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0

    async def aclose(self):
        """记录测试客户端关闭。"""
        self.closed = True


def _index(name: str) -> dict:
    """生成四类字段完整的最小符号索引。

    参数:
        - `name`：用于证明返回索引未被其他项目覆盖的类名。
    """
    return {
        "symbols": [{"symbol_name": name, "file": "php/Example.php", "line": 1}],
        "references": [],
        "configs": [],
        "sql": [],
    }


def test_encode_and_decode_index_round_trip() -> None:
    """验证压缩缓存不会改变符号、引用、配置和 SQL 结构。"""
    index = _index("PersonalAssetLevelService")
    assert decode_index(encode_index(index)) == index


def test_repository_fingerprint_changes_when_php_file_changes(tmp_path: Path) -> None:
    """验证代码变化后不会命中旧快照。

    参数:
        - `tmp_path`：pytest 提供的临时仓库根目录。
    """
    project_dir = tmp_path / "waky3" / "php"
    project_dir.mkdir(parents=True)
    source_file = project_dir / "Example.php"
    source_file.write_text("<?php class Example {}", encoding="utf-8")
    before = repository_fingerprint(str(tmp_path), "waky3")
    source_file.write_text("<?php class Example { public function run() {} }", encoding="utf-8")
    after = repository_fingerprint(str(tmp_path), "waky3")
    assert before != after


async def test_concurrent_tasks_share_one_project_index_build() -> None:
    """验证同项目并发任务只扫描一次仓库，其他任务读取共享缓存。"""
    redis = _Redis()
    build_calls = 0

    def build(root: str, project: str) -> dict:
        """模拟耗时仓库扫描并记录调用次数。

        参数:
            - `root`：测试仓库根目录，类型为 `str`。
            - `project`：测试项目标识，类型为 `str`。
        """
        nonlocal build_calls
        build_calls += 1
        time.sleep(0.08)
        return _index("AwardQueueService")

    async def load():
        """按生产参数读取同一项目快照。"""
        return await load_project_symbol_index(
            root="/repositories",
            project="waky3",
            redis_url="redis://unused/0",
            redis_client=redis,
            index_builder=build,
            fingerprint="a" * 64,
            wait_timeout_seconds=2,
            poll_interval_seconds=0.01,
        )

    results = await asyncio.gather(*(load() for _ in range(4)))

    assert build_calls == 1
    assert {item.cache_status for item in results} == {"built", "redis_hit_after_wait"}
    assert all(item.index == results[0].index for item in results)


async def test_warm_cache_skips_repository_scan() -> None:
    """验证缓存命中时不再执行仓库扫描。"""
    redis = _Redis()
    fingerprint = "b" * 64
    first = await load_project_symbol_index(
        root="/repositories",
        project="waky4",
        redis_url="redis://unused/0",
        redis_client=redis,
        index_builder=lambda root, project: _index("GiftWeekStarSettlementService"),
        fingerprint=fingerprint,
    )

    def should_not_build(root: str, project: str) -> dict:
        """缓存命中时禁止再次扫描仓库。

        参数:
            - `root`：测试仓库根目录，类型为 `str`。
            - `project`：测试项目标识，类型为 `str`。
        """
        raise AssertionError("缓存命中时不应扫描仓库")

    second = await load_project_symbol_index(
        root="/repositories",
        project="waky4",
        redis_url="redis://unused/0",
        redis_client=redis,
        index_builder=should_not_build,
        fingerprint=fingerprint,
    )

    assert first.cache_status == "built"
    assert second.cache_status == "redis_hit"
    assert second.index == first.index
