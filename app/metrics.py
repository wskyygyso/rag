"""Prometheus 兼容的运行指标采样和文本输出。"""

import asyncio
import bisect
import math
import re
import time
from threading import Lock
from typing import Dict, List, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api", tags=["observability"])

_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_STARTED_AT = time.monotonic()
_LOCK = Lock()
_HTTP_REQUESTS: Dict[Tuple[str, str, int], int] = {}
_HTTP_DURATIONS: Dict[Tuple[str, str, int], Tuple[List[int], float, int]] = {}
_IN_FLIGHT = 0
_DATABASE_COLLECTION_ERRORS = 0
_DATABASE_TIMEOUT_SECONDS = 3.0

_DYNAMIC_PATH_PATTERNS = (
    (re.compile(r"^/api/diagnoses/[^/]+/feedback/summary$"), "/api/diagnoses/{task_id}/feedback/summary"),
    (re.compile(r"^/api/diagnoses/[^/]+/feedback$"), "/api/diagnoses/{task_id}/feedback"),
    (re.compile(r"^/api/diagnoses/[^/]+/report$"), "/api/diagnoses/{task_id}/report"),
    (re.compile(r"^/api/diagnoses/[^/]+/trace$"), "/api/diagnoses/{task_id}/trace"),
    (re.compile(r"^/api/diagnoses/[^/]+/events$"), "/api/diagnoses/{task_id}/events"),
    (re.compile(r"^/api/diagnoses/[^/]+/cancel$"), "/api/diagnoses/{task_id}/cancel"),
    (re.compile(r"^/api/diagnoses/[^/]+/retry$"), "/api/diagnoses/{task_id}/retry"),
    (re.compile(r"^/api/diagnoses/[^/]+/operations$"), "/api/diagnoses/{task_id}/operations"),
    (re.compile(r"^/api/diagnoses/[^/]+$"), "/api/diagnoses/{task_id}"),
    (re.compile(r"^/api/feedback/[^/]+/summary$"), "/api/feedback/{project}/summary"),
    (re.compile(r"^/api/code/[^/]+/(symbols|references|call-chain)$"), "/api/code/{project}/{operation}"),
    (re.compile(r"^/api/search/[^/]+/hybrid$"), "/api/search/{project}/hybrid"),
    (re.compile(r"^/api/index/[^/]+/runs/[^/]+/retry$"), "/api/index/{project}/runs/{run_id}/retry"),
    (re.compile(r"^/api/index/[^/]+/runs$"), "/api/index/{project}/runs"),
)


def reset_metrics() -> None:
    """清空进程内 HTTP 指标，仅用于测试隔离。"""
    global _IN_FLIGHT, _DATABASE_COLLECTION_ERRORS
    with _LOCK:
        _HTTP_REQUESTS.clear()
        _HTTP_DURATIONS.clear()
        _IN_FLIGHT = 0
        _DATABASE_COLLECTION_ERRORS = 0


def begin_http_request() -> float:
    """增加并发请求计数并返回本次请求的单调时钟起点。"""
    global _IN_FLIGHT
    with _LOCK:
        _IN_FLIGHT += 1
    return time.perf_counter()


def route_template(request: Request) -> str:
    """优先使用已匹配路由模板，否则将动态路径归一化，限制标签基数。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path:
        return route_path
    path = request.url.path
    for pattern, template in _DYNAMIC_PATH_PATTERNS:
        if pattern.match(path):
            return template
    known_paths = {
        "/",
        "/health",
        "/ui",
        "/api/metrics",
        "/api/diagnoses",
        "/api/diagnoses/history",
        "/api/validation/database/query",
        "/api/validation/config/read",
        "/api/validation/logs/search",
    }
    return path if path in known_paths else "unmatched"


def finish_http_request(request: Request, started_at: float, status_code: int) -> None:
    """记录请求计数、状态码和延迟直方图，并减少并发请求计数。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
        - `started_at`：请求开始或运行开始时间，类型为 `float`。
        - `status_code`：HTTP 响应状态码，类型为 `int`。
    """
    global _IN_FLIGHT
    duration = max(0.0, time.perf_counter() - started_at)
    key = (request.method, route_template(request), status_code)
    bucket_index = bisect.bisect_left(_BUCKETS, duration)
    with _LOCK:
        _HTTP_REQUESTS[key] = _HTTP_REQUESTS.get(key, 0) + 1
        bucket_counts, total, count = _HTTP_DURATIONS.get(
            key, ([0] * (len(_BUCKETS) + 1), 0.0, 0)
        )
        bucket_counts[bucket_index] += 1
        _HTTP_DURATIONS[key] = (bucket_counts, total + duration, count + 1)
        _IN_FLIGHT = max(0, _IN_FLIGHT - 1)


def _escape_label(value: str) -> str:
    """转义 Prometheus 标签中的反斜杠、引号和换行。

    参数:
        - `value`：待写入 Prometheus 标签的原始文本，类型为 `str`。
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**values: str) -> str:
    """生成稳定排序的 Prometheus 标签集合。

    参数:
        - `**values`：Prometheus 标签键值集合。
    """
    content = ",".join(
        f'{key}="{_escape_label(str(value))}"' for key, value in sorted(values.items())
    )
    return f"{{{content}}}" if content else ""


def render_runtime_metrics() -> str:
    """渲染进程内 HTTP、并发和运行时长指标。"""
    with _LOCK:
        requests_snapshot = dict(_HTTP_REQUESTS)
        duration_snapshot = {
            key: (list(value[0]), value[1], value[2])
            for key, value in _HTTP_DURATIONS.items()
        }
        in_flight = _IN_FLIGHT
        database_errors = _DATABASE_COLLECTION_ERRORS

    lines = [
        "# HELP ragent_http_requests_total 已完成的 HTTP 请求总数。",
        "# TYPE ragent_http_requests_total counter",
    ]
    for (method, path, status_code), count in sorted(requests_snapshot.items()):
        labels = _labels(method=method, path=path, status=str(status_code))
        lines.append(f"ragent_http_requests_total{labels} {count}")

    lines.extend(
        [
            "# HELP ragent_http_request_duration_seconds HTTP 请求耗时。",
            "# TYPE ragent_http_request_duration_seconds histogram",
        ]
    )
    for (method, path, status_code), (bucket_counts, total, count) in sorted(
        duration_snapshot.items()
    ):
        base_labels = {"method": method, "path": path, "status": str(status_code)}
        cumulative = 0
        for boundary, bucket_count in zip(_BUCKETS, bucket_counts[:-1]):
            cumulative += bucket_count
            labels = _labels(**base_labels, le=f"{boundary:g}")
            lines.append(f"ragent_http_request_duration_seconds_bucket{labels} {cumulative}")
        labels = _labels(**base_labels, le="+Inf")
        lines.append(f"ragent_http_request_duration_seconds_bucket{labels} {count}")
        labels = _labels(**base_labels)
        lines.append(f"ragent_http_request_duration_seconds_sum{labels} {total:.9g}")
        lines.append(f"ragent_http_request_duration_seconds_count{labels} {count}")

    lines.extend(
        [
            "# HELP ragent_http_in_flight_requests 当前正在处理的 HTTP 请求数。",
            "# TYPE ragent_http_in_flight_requests gauge",
            f"ragent_http_in_flight_requests {in_flight}",
            "# HELP ragent_process_uptime_seconds 当前 API 进程运行时长。",
            "# TYPE ragent_process_uptime_seconds gauge",
            f"ragent_process_uptime_seconds {max(0.0, time.monotonic() - _STARTED_AT):.9g}",
            "# HELP ragent_database_metrics_collection_errors_total 数据库指标采集失败次数。",
            "# TYPE ragent_database_metrics_collection_errors_total counter",
            f"ragent_database_metrics_collection_errors_total {database_errors}",
        ]
    )
    return "\n".join(lines) + "\n"


async def render_database_metrics(pool) -> str:
    """查询任务状态、任务操作和安全拒绝聚合指标，采集失败时标记数据库不可用。

    参数:
        - `pool`：数据库连接池或测试替身。
    """
    global _DATABASE_COLLECTION_ERRORS
    try:
        task_rows, operation_rows, security_rows, trace_rows = await asyncio.wait_for(
            asyncio.gather(
                pool.fetch(
                    "SELECT status, COUNT(*) AS count FROM diagnostic_tasks GROUP BY status"
                ),
                pool.fetch(
                    "SELECT operation, COUNT(*) AS count FROM diagnostic_task_operations GROUP BY operation"
                ),
                pool.fetch(
                    "SELECT event_type, status_code, COUNT(*) AS count FROM security_audit GROUP BY event_type, status_code"
                ),
                pool.fetch(
                    "SELECT stage, status, COUNT(*) AS count, "
                    "COALESCE(AVG(elapsed_ms), 0) AS average_elapsed_ms, "
                    "COALESCE(MAX(elapsed_ms), 0) AS max_elapsed_ms "
                    "FROM diagnosis_trace_spans GROUP BY stage, status"
                ),
            ),
            timeout=_DATABASE_TIMEOUT_SECONDS,
        )
    except Exception:
        with _LOCK:
            _DATABASE_COLLECTION_ERRORS += 1
        return "\n".join(
            [
                "# HELP ragent_database_up 数据库指标采集是否成功。",
                "# TYPE ragent_database_up gauge",
                "ragent_database_up 0",
            ]
        ) + "\n"

    lines = [
        "# HELP ragent_database_up 数据库指标采集是否成功。",
        "# TYPE ragent_database_up gauge",
        "ragent_database_up 1",
        "# HELP ragent_diagnosis_tasks 各状态的诊断任务数量。",
        "# TYPE ragent_diagnosis_tasks gauge",
    ]
    for row in task_rows:
        labels = _labels(status=str(row["status"]))
        lines.append(f"ragent_diagnosis_tasks{labels} {int(row['count'])}")
    lines.extend(
        [
            "# HELP ragent_task_operations_total 各类型任务操作累计数量。",
            "# TYPE ragent_task_operations_total counter",
        ]
    )
    for row in operation_rows:
        labels = _labels(operation=str(row["operation"]))
        lines.append(f"ragent_task_operations_total{labels} {int(row['count'])}")
    lines.extend(
        [
            "# HELP ragent_security_denials_total 认证和授权拒绝累计数量。",
            "# TYPE ragent_security_denials_total counter",
        ]
    )
    for row in security_rows:
        labels = _labels(
            event_type=str(row["event_type"]), status_code=str(row["status_code"])
        )
        lines.append(f"ragent_security_denials_total{labels} {int(row['count'])}")
    lines.extend(
        [
            "# HELP ragent_diagnosis_stage_events 各诊断阶段和结果状态的轨迹数量。",
            "# TYPE ragent_diagnosis_stage_events gauge",
            "# HELP ragent_diagnosis_stage_duration_ms 各诊断阶段平均耗时毫秒数。",
            "# TYPE ragent_diagnosis_stage_duration_ms gauge",
            "# HELP ragent_diagnosis_stage_max_duration_ms 各诊断阶段最大耗时毫秒数。",
            "# TYPE ragent_diagnosis_stage_max_duration_ms gauge",
        ]
    )
    for row in trace_rows:
        stage = str(row.get("stage") or "unknown")
        status = str(row.get("status") or "unknown")
        labels = _labels(stage=stage, status=status)
        lines.append(f"ragent_diagnosis_stage_events{labels} {int(row.get('count') or 0)}")
        duration_labels = _labels(stage=stage, status=status)
        lines.append(
            f"ragent_diagnosis_stage_duration_ms{duration_labels} "
            f"{float(row.get('average_elapsed_ms') or 0):.9g}"
        )
        lines.append(
            f"ragent_diagnosis_stage_max_duration_ms{duration_labels} "
            f"{float(row.get('max_elapsed_ms') or 0):.9g}"
        )
    return "\n".join(lines) + "\n"


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    """返回 Prometheus 可抓取的文本指标；启用认证时沿用 API Key 保护。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        database_content = "\n".join(
            [
                "# HELP ragent_database_up 数据库指标采集是否成功。",
                "# TYPE ragent_database_up gauge",
                "ragent_database_up 0",
            ]
        ) + "\n"
    else:
        database_content = await render_database_metrics(pool)
    content = render_runtime_metrics() + database_content
    return PlainTextResponse(
        content,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
