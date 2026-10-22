"""进程内和 Redis 共享请求限流工具。"""

import hashlib
import math
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict

from redis import asyncio as redis_asyncio


class RateLimiter:
    """按调用方标识维护固定时间窗口计数的限流器。"""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        """创建限流器；``limit`` 必须为正数，窗口必须大于零。"""
        if limit < 1 or window_seconds <= 0:
            raise ValueError("限流阈值和时间窗口必须为正数")
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """判断调用方在当前窗口内是否还可以执行一次请求。"""
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            cutoff = now - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class RedisRateLimiter:
    """使用 Redis Lua 原子计数实现跨实例固定窗口限流。"""

    _ALLOW_SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """

    def __init__(self, client, limit: int, window_seconds: int, namespace: str = "rag:rate") -> None:
        """创建共享限流器；客户端需兼容 redis.asyncio 的 eval 接口。"""
        if limit < 1 or window_seconds <= 0:
            raise ValueError("限流阈值和时间窗口必须为正数")
        self.client = client
        self.limit = limit
        self.window_seconds = window_seconds
        self.namespace = namespace

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        limit: int,
        window_seconds: int,
        namespace: str = "rag:rate",
    ) -> "RedisRateLimiter":
        """根据 Redis URL 创建共享限流器，连接在实际请求时惰性建立。"""
        client = redis_asyncio.from_url(redis_url, decode_responses=True)
        return cls(client, limit, window_seconds, namespace)

    async def allow(self, key: str) -> bool:
        """原子增加窗口计数并返回当前请求是否未超过限制。"""
        bucket = math.floor(time.time() / self.window_seconds)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        redis_key = f"{self.namespace}:{digest}:{bucket}"
        count = await self.client.eval(
            self._ALLOW_SCRIPT,
            1,
            redis_key,
            self.window_seconds + 1,
        )
        return int(count) <= self.limit

    async def close(self) -> None:
        """关闭底层 Redis 连接，释放应用生命周期持有的资源。"""
        await self.client.aclose()
