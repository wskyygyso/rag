"""请求 ID 和结构化访问日志测试。"""

import logging

from fastapi.testclient import TestClient

from app.main import app
from app.request_context import resolve_request_id


def test_request_id_validation_and_generation() -> None:
    """验证合法 ID 被复用，非法字符和超长 ID 被替换。"""
    assert resolve_request_id("review-123.abc") == "review-123.abc"
    assert resolve_request_id("bad\nvalue") != "bad\nvalue"
    generated = resolve_request_id("x" * 129)
    assert len(generated) == 32


def test_response_echoes_safe_request_id() -> None:
    """验证响应头回传合法请求 ID。"""
    response = TestClient(app).get(
        "/health/live", headers={"X-Request-ID": "request-abc-123"}
    )
    assert response.headers["x-request-id"] == "request-abc-123"


def test_access_log_excludes_query_and_api_key(caplog) -> None:
    """验证访问日志不包含查询参数和 API Key。"""
    logger = logging.getLogger("ragent.access")
    caplog.set_level(logging.INFO, logger=logger.name)
    response = TestClient(app).get(
        "/health/live?token=query-secret",
        headers={"X-API-Key": "header-secret", "X-Request-ID": "log-test-1"},
    )
    assert response.status_code == 200
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "log-test-1" in messages
    assert '"path": "/health/live"' in messages
    assert "query-secret" not in messages
    assert "header-secret" not in messages
