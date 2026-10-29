"""HTTP 安全响应头和 CORS 配置测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.security_headers import add_cors_middleware, parse_cors_origins


def test_security_headers_are_applied_to_public_and_api_responses() -> None:
    """验证公开页面和 API 响应包含通用安全头。"""
    client = TestClient(app)
    public = client.get("/health/live")
    api = client.get("/api/metrics")
    for response in (public, api):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert api.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in public.headers


def test_hsts_is_added_for_https_forwarded_request() -> None:
    """验证 HTTPS 反向代理请求添加 HSTS。"""
    response = TestClient(app).get(
        "/health/live", headers={"X-Forwarded-Proto": "https"}
    )
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_cors_origins_and_preflight_headers_are_restricted() -> None:
    """验证 CORS 只允许配置来源并接受认证请求头。"""
    assert parse_cors_origins("https://a.example, https://b.example/, ") == [
        "https://a.example",
        "https://b.example",
    ]
    test_app = FastAPI()
    add_cors_middleware(test_app, "https://a.example")

    @test_app.get("/api/example")
    async def example():
        """提供 CORS 测试接口。"""
        return {"status": "ok"}

    client = TestClient(test_app)
    allowed = client.options(
        "/api/example",
        headers={
            "Origin": "https://a.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )
    denied = client.get("/api/example", headers={"Origin": "https://evil.example"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://a.example"
    assert "X-API-Key" in allowed.headers["access-control-allow-headers"]
    assert "access-control-allow-origin" not in denied.headers
