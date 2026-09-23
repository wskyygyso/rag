"""轻量级进程内请求限流工具。

该实现用于单实例开发和基础保护；生产多实例部署应将计数器替换为 Redis 等共享存储。
"""

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict


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
