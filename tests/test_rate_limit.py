"""验证接口限流工具测试。"""

import time

import pytest

from app.rate_limit import RateLimiter


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
