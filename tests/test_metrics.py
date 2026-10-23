"""HTTP 和数据库运行指标测试。"""

from fastapi import Request
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import (
    begin_http_request,
    finish_http_request,
    render_runtime_metrics,
    reset_metrics,
    route_template,
)


def _request(method: str, path: str) -> Request:
    """构造只包含指标测试所需字段的 HTTP 请求。"""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def test_runtime_metrics_record_requests_and_normalize_dynamic_paths() -> None:
    """验证请求计数、延迟直方图和动态路径模板归一化。"""
    reset_metrics()
    request = _request("GET", "/api/diagnoses/diag_secret/report")
    assert route_template(request) == "/api/diagnoses/{task_id}/report"
    started_at = begin_http_request()
    finish_http_request(request, started_at, 200)
    content = render_runtime_metrics()
    assert 'ragent_http_requests_total{method="GET",path="/api/diagnoses/{task_id}/report",status="200"} 1' in content
    assert "ragent_http_request_duration_seconds_bucket" in content
    assert "ragent_http_in_flight_requests 0" in content
    assert "diag_secret" not in content
    reset_metrics()


class _Pool:
    """返回固定任务、操作和安全拒绝聚合数据。"""

    async def fetch(self, query, *args):
        """按 SQL 来源返回不同指标行。"""
        if "FROM diagnostic_tasks" in query:
            return [{"status": "COMPLETED", "count": 2}]
        if "FROM diagnostic_task_operations" in query:
            return [{"operation": "CREATE", "count": 3}]
        return [{"event_type": "AUTHENTICATION", "status_code": 401, "count": 4}]


def test_metrics_endpoint_exposes_http_and_database_metrics() -> None:
    """验证指标端点输出 HTTP、任务、操作和安全拒绝指标。"""
    reset_metrics()
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).get("/api/metrics")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "ragent_database_up 1" in response.text
    assert 'ragent_diagnosis_tasks{status="COMPLETED"} 2' in response.text
    assert 'ragent_task_operations_total{operation="CREATE"} 3' in response.text
    assert 'ragent_security_denials_total{event_type="AUTHENTICATION",status_code="401"} 4' in response.text
    reset_metrics()


class _BrokenPool:
    """模拟数据库指标查询失败。"""

    async def fetch(self, query, *args):
        """抛出数据库异常。"""
        raise RuntimeError("database unavailable")


def test_metrics_endpoint_marks_database_unavailable() -> None:
    """验证数据库指标采集失败时仍返回可抓取指标。"""
    reset_metrics()
    app.state.db_pool = _BrokenPool()
    try:
        response = TestClient(app).get("/api/metrics")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert "ragent_database_up 0" in response.text
    assert "ragent_database_metrics_collection_errors_total 1" in response.text
    reset_metrics()
