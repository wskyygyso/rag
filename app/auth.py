"""API Key 身份认证、读写权限和项目访问边界。"""

import hmac
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Awaitable, Callable, Dict, FrozenSet, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.settings import Settings, get_settings

PUBLIC_PATHS = {"/", "/health", "/ui", "/docs", "/redoc", "/openapi.json"}
VALID_SCOPES = {"read", "write"}


@dataclass(frozen=True)
class ApiIdentity:
    """表示通过 API Key 识别出的调用方及其权限范围。"""

    subject: str
    projects: FrozenSet[str]
    scopes: FrozenSet[str]


@lru_cache(maxsize=8)
def _parse_api_keys(raw_config: str) -> Dict[str, ApiIdentity]:
    """解析 API Key JSON 配置，非法配置直接拒绝而不是静默放行。"""
    try:
        config = json.loads(raw_config or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("API_KEYS_JSON 不是合法 JSON") from exc
    if not isinstance(config, dict):
        raise ValueError("API_KEYS_JSON 必须是对象")

    identities = {}
    for api_key, value in config.items():
        if not isinstance(api_key, str) or len(api_key) < 16:
            raise ValueError("API Key 长度至少为 16 个字符")
        if not isinstance(value, dict):
            raise ValueError("API Key 权限配置必须是对象")
        subject = value.get("subject")
        projects = value.get("projects")
        scopes = value.get("scopes")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("API Key 必须配置 subject")
        if not isinstance(projects, list) or not projects or not all(
            isinstance(project, str) and project.strip() for project in projects
        ):
            raise ValueError("API Key 的 projects 必须是非空字符串数组")
        if not isinstance(scopes, list) or not scopes or not all(
            isinstance(scope, str) for scope in scopes
        ):
            raise ValueError("API Key 的 scopes 必须是非空数组")
        invalid_scopes = set(scopes) - VALID_SCOPES
        if invalid_scopes:
            raise ValueError(f"API Key 包含未知 scope：{sorted(invalid_scopes)}")
        identities[api_key] = ApiIdentity(
            subject=subject.strip(),
            projects=frozenset(project.strip() for project in projects),
            scopes=frozenset(scopes),
        )
    return identities


def validate_auth_settings(settings: Settings) -> None:
    """在应用启动时校验认证配置，启用认证后不得使用空密钥集合。"""
    if not settings.auth_enabled:
        return
    if not _parse_api_keys(settings.api_keys_json):
        raise ValueError("启用认证时必须配置至少一个 API Key")


def _presented_key(request: Request) -> Optional[str]:
    """从 X-API-Key 或 Bearer Authorization 中提取调用方密钥。"""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return None


def authenticate_request(request: Request, settings: Settings) -> Optional[ApiIdentity]:
    """校验 API Key 和请求方法所需 scope，返回调用方身份。"""
    if not settings.auth_enabled:
        return None
    try:
        identities = _parse_api_keys(settings.api_keys_json)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    presented = _presented_key(request)
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    identity = next(
        (
            candidate
            for api_key, candidate in identities.items()
            if hmac.compare_digest(api_key, presented)
        ),
        None,
    )
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key 无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    required_scope = "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write"
    if required_scope not in identity.scopes:
        raise HTTPException(status_code=403, detail=f"缺少 {required_scope} 权限")
    return identity


async def auth_guard(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    settings: Settings,
) -> Response:
    """保护所有 API 路径；健康检查、文档和前端页面保持公开。"""
    path = request.url.path
    if not settings.auth_enabled or not path.startswith("/api/") or path in PUBLIC_PATHS:
        return await call_next(request)
    try:
        request.state.api_identity = authenticate_request(request, settings)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )
    return await call_next(request)


def api_identity(request: Request) -> Optional[ApiIdentity]:
    """读取中间件认证后写入请求上下文的调用方身份。"""
    return getattr(request.state, "api_identity", None)


def require_project_access(request: Request, project: Optional[str]) -> None:
    """校验当前调用方是否允许访问指定项目；未启用认证时保持原行为。"""
    identity = api_identity(request)
    if identity is None:
        if get_settings().auth_enabled:
            raise HTTPException(status_code=401, detail="缺少认证身份")
        return
    if not project:
        raise HTTPException(status_code=403, detail="启用访问控制时必须指定项目")
    if "*" not in identity.projects and project not in identity.projects:
        raise HTTPException(status_code=403, detail=f"无权访问项目：{project}")


def operation_actor(request: Request, fallback: Optional[str] = None) -> Optional[str]:
    """优先使用可信认证身份作为操作审计主体，开发模式兼容 X-Actor。"""
    identity = api_identity(request)
    if identity is not None:
        return identity.subject
    return request.headers.get("X-Actor") or fallback
