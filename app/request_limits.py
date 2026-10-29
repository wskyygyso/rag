"""HTTP 请求体大小限制。"""

from fastapi import Request


class RequestBodyTooLarge(ValueError):
    """请求体超过允许大小时抛出的业务异常。"""


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """读取并缓存请求体，超过限制时停止读取并拒绝请求。"""
    if max_bytes < 1:
        raise ValueError("请求体大小上限必须为正数")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise RequestBodyTooLarge("Content-Length 无效") from exc
        if declared_length > max_bytes:
            raise RequestBodyTooLarge("请求体超过大小限制")

    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise RequestBodyTooLarge("请求体超过大小限制")
        chunks.append(chunk)
    body = b"".join(chunks)
    # 下游路由复用已缓存请求体，避免再次消费网络流。
    request._body = body
    return body
