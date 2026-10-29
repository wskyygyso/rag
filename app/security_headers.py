"""HTTP 安全响应头和 CORS 白名单配置。"""

from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response


def parse_cors_origins(raw_origins: str) -> List[str]:
    """解析逗号分隔的 CORS 来源，去空、去重并保持顺序。"""
    origins = []
    for origin in (raw_origins or "").split(","):
        value = origin.strip().rstrip("/")
        if value and value not in origins:
            origins.append(value)
    return origins


def add_cors_middleware(application: FastAPI, raw_origins: str) -> List[str]:
    """在白名单非空时启用 CORS，并返回实际允许的来源。"""
    origins = parse_cors_origins(raw_origins)
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Actor"],
            expose_headers=["Content-Disposition"],
            allow_credentials=False,
            max_age=600,
        )
    return origins


def apply_security_headers(response: Response, request: Request) -> None:
    """补充通用安全响应头，并在 HTTPS 代理请求上添加 HSTS。"""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    if forwarded_proto.lower() == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
