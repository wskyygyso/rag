"""性能基线脚本统计和并发执行测试。"""

import httpx
import pytest

from scripts.performance_baseline import percentile, run_baseline, summarize_samples


def test_percentile_and_summary_are_stable() -> None:
    """验证最近秩百分位、错误率和吞吐计算。"""
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert percentile(values, 0.50) == 3.0
    assert percentile(values, 0.95) == 100.0
    summary = summarize_samples(values, {"200": 4, "500": 1}, 1, 2.0)
    assert summary["error_rate"] == 0.2
    assert summary["requests_per_second"] == 2.5
    assert summary["latency_ms"]["p95"] == 100.0


@pytest.mark.asyncio
async def test_run_baseline_counts_http_errors_and_successes() -> None:
    """验证模拟 HTTP 200/500 响应被正确统计。"""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        """交替返回成功和失败响应。

        参数:
            - `request`：FastAPI 请求上下文，类型为 `httpx.Request`。
        """
        calls["count"] += 1
        return httpx.Response(200 if calls["count"] % 2 else 500)

    async with httpx.AsyncClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await run_baseline(client, "/health", requests_count=6, concurrency=3)
    assert result["requests"] == 6
    assert result["errors"] == 3
    assert result["status_counts"] == {"200": 3, "500": 3}


def test_percentile_rejects_invalid_ratio() -> None:
    """验证非法百分位比例被拒绝。"""
    with pytest.raises(ValueError):
        percentile([1.0], 0)
