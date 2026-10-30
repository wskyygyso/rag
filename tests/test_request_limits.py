"""请求体大小限制测试。"""

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.request_limits import RequestBodyTooLarge, read_limited_body


def _stream_request(chunks):
    """构造能按块返回请求体的 Starlette 请求。

    参数:
        - `chunks`：待写入或待统计的文档分块集合。
    """
    messages = iter([{"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks])
    messages = iter(list(messages) + [{"type": "http.request", "body": b"", "more_body": False}])

    async def receive():
        """返回下一段请求体。"""
        return next(messages)

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/test",
            "raw_path": b"/api/test",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_streamed_body_rejects_after_limit_without_buffering_rest() -> None:
    """验证无 Content-Length 的流在超限后立即拒绝。"""
    request = _stream_request([b"12345", b"67890"])
    with pytest.raises(RequestBodyTooLarge):
        await read_limited_body(request, 8)


def test_api_rejects_oversized_request_body(monkeypatch) -> None:
    """验证 API 中间件在业务解析前返回 413。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    previous = main.settings.max_request_body_bytes
    main.settings.max_request_body_bytes = 20
    try:
        response = TestClient(app).post(
            "/api/diagnoses",
            content=b"x" * 21,
            headers={"Content-Type": "application/json"},
        )
    finally:
        main.settings.max_request_body_bytes = previous
    assert response.status_code == 413
    assert response.json()["detail"] == "请求体超过大小限制"
