"""RAgent HTTP 并发性能基线脚本。"""

import argparse
import asyncio
import json
import math
import os
import time
from typing import Any, Dict, List, Sequence

import httpx


def percentile(values: Sequence[float], ratio: float) -> float:
    """使用最近秩法计算百分位，空样本返回 0。

    参数:
        - `values`：Prometheus 标签键值集合，类型为 `Sequence[float]`。
        - `ratio`：百分位比例，类型为 `float`。
    """
    if not values:
        return 0.0
    if ratio <= 0 or ratio > 1:
        raise ValueError("百分位比例必须在 0 到 1 之间")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * ratio) - 1)
    return ordered[index]


def summarize_samples(
    latencies_ms: List[float],
    status_counts: Dict[str, int],
    errors: int,
    elapsed_seconds: float,
) -> Dict[str, Any]:
    """汇总延迟、错误率、状态码和吞吐指标。

    参数:
        - `latencies_ms`：请求延迟毫秒样本，类型为 `List[float]`。
        - `status_counts`：HTTP 状态码计数，类型为 `Dict[str, int]`。
        - `errors`：性能测试失败请求数，类型为 `int`。
        - `elapsed_seconds`：性能测试总耗时，类型为 `float`。
    """
    total = len(latencies_ms)
    error_rate = errors / total if total else 1.0
    return {
        "requests": total,
        "errors": errors,
        "error_rate": error_rate,
        "requests_per_second": total / elapsed_seconds if elapsed_seconds > 0 else 0.0,
        "latency_ms": {
            "min": round(min(latencies_ms), 3) if latencies_ms else 0.0,
            "mean": round(sum(latencies_ms) / total, 3) if total else 0.0,
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "p99": round(percentile(latencies_ms, 0.99), 3),
            "max": round(max(latencies_ms), 3) if latencies_ms else 0.0,
        },
        "status_counts": dict(sorted(status_counts.items())),
    }


async def run_baseline(
    client: httpx.AsyncClient,
    path: str,
    requests_count: int,
    concurrency: int,
) -> Dict[str, Any]:
    """按指定并发执行请求，并返回可机器读取的性能摘要。

    参数:
        - `client`：HTTP 异步客户端，类型为 `httpx.AsyncClient`。
        - `path`：文件或请求路径，类型为 `str`。
        - `requests_count`：性能测试总请求数，类型为 `int`。
        - `concurrency`：并发请求数，类型为 `int`。
    """
    if requests_count < 1 or concurrency < 1:
        raise ValueError("请求数和并发数必须为正数")
    semaphore = asyncio.Semaphore(concurrency)
    latencies_ms: List[float] = []
    status_counts: Dict[str, int] = {}
    errors = 0

    async def request_once() -> None:
        """执行单次请求并记录延迟和状态；网络异常计为失败。"""
        nonlocal errors
        async with semaphore:
            started_at = time.perf_counter()
            try:
                response = await client.get(path)
                status = str(response.status_code)
                status_counts[status] = status_counts.get(status, 0) + 1
                if response.status_code >= 400:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
                status_counts["network_error"] = status_counts.get("network_error", 0) + 1
            finally:
                latencies_ms.append((time.perf_counter() - started_at) * 1000)

    started_at = time.perf_counter()
    await asyncio.gather(*(request_once() for _ in range(requests_count)))
    return summarize_samples(
        latencies_ms,
        status_counts,
        errors,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def parse_args() -> argparse.Namespace:
    """解析性能基线命令行参数，API Key 只从环境变量读取。"""
    parser = argparse.ArgumentParser(description="RAgent HTTP 并发性能基线")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    """执行基线并返回阈值检查退出码。

    参数:
        - `args`：可变位置参数集合，类型为 `argparse.Namespace`。
    """
    headers = {}
    api_key = os.getenv("RAGENT_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=headers,
        timeout=args.timeout,
    ) as client:
        result = await run_baseline(client, args.path, args.requests, args.concurrency)
    result["thresholds"] = {
        "max_error_rate": args.max_error_rate,
        "max_p95_ms": args.max_p95_ms,
    }
    result["passed"] = (
        result["error_rate"] <= args.max_error_rate
        and result["latency_ms"]["p95"] <= args.max_p95_ms
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def main() -> None:
    """脚本入口：运行性能基线并以阈值结果决定进程退出码。"""
    raise SystemExit(asyncio.run(main_async(parse_args())))


if __name__ == "__main__":
    main()
