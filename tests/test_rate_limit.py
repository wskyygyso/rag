"""验证接口限流工具测试。"""

import time

import pytest

from app.rate_limit import RateLimiter, RedisRateLimiter


def test_rate_limiter_rejects_after_limit_and_allows_next_window(monkeypatch) -> None:
    """验证固定窗口内超限，窗口过期后恢复请求。"""
    current = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: current[0])
    limiter = RateLimiter(2, 10)
    assert limiter.allow("client") is True
    assert limiter.allow("client") is True
    assert limiter.allow("client") is False
    current[0] = 111.0
    assert limiter.allow("client") is True


def test_rate_limiter_rejects_invalid_configuration() -> None:
    """验证限流阈值和窗口不能配置为零或负数。"""
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(1, 0)


class _Redis:
    """模拟共享 Redis 计数和过期设置。"""

    def __init__(self):
        """初始化共享计数状态。"""
        self.counts = {}
        self.expirations = []

    async def eval(self, script, key_count, key, ttl):
        """原子增加计数并记录过期时间。"""
        self.counts[key] = self.counts.get(key, 0) + 1
        self.expirations.append((key, ttl))
        return self.counts[key]

    async def aclose(self):
        """模拟关闭 Redis 连接。"""
        return None


@pytest.mark.asyncio
async def test_redis_rate_limiter_shares_count_across_instances() -> None:
    """验证两个限流器实例通过同一 Redis 计数共享额度和 TTL。"""
    client = _Redis()
    first = RedisRateLimiter(client, 2, 60)
    second = RedisRateLimiter(client, 2, 60)
    assert await first.allow("client") is True
    assert await second.allow("client") is True
    assert await first.allow("client") is False
    assert len(client.expirations) == 3
    assert all(ttl == 61 for _, ttl in client.expirations)
