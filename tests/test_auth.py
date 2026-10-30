"""API Key 认证和项目权限边界测试。"""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.auth import ApiIdentity, auth_guard, require_project_access, validate_auth_settings
from app.code_api import router as code_router
from app.settings import Settings

API_KEY = "test-api-key-with-enough-length"


def _settings(scopes=None) -> Settings:
    """返回启用认证的测试配置，默认同时具备读写权限。

    参数:
        - `scopes`：测试认证主体允许的读写权限。
    """
    config = {
        API_KEY: {
            "subject": "reviewer",
            "projects": ["waky3"],
            "scopes": scopes or ["read", "write"],
        }
    }
    return Settings(
        auth_enabled=True,
        api_keys_json=json.dumps(config, ensure_ascii=False),
    )


def test_api_key_middleware_enforces_key_scope_and_public_paths() -> None:
    """验证缺失密钥、只读 scope、写权限和公开健康检查。"""
    settings = _settings()
    test_app = FastAPI()

    @test_app.middleware("http")
    async def access_control(request, call_next):
        """测试应用复用生产环境的认证中间件。

        参数:
            - `request`：FastAPI 请求上下文。
            - `call_next`：继续调用下游中间件或路由的异步函数。
        """
        return await auth_guard(request, call_next, settings)

    @test_app.get("/health")
    async def health():
        """提供公开健康检查。"""
        return {"status": "ok"}

    @test_app.get("/api/read")
    async def read_endpoint():
        """提供受保护的读接口。"""
        return {"status": "read"}

    @test_app.post("/api/write")
    async def write_endpoint():
        """提供受保护的写接口。"""
        return {"status": "write"}

    client = TestClient(test_app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/read").status_code == 401
    assert client.get("/api/read", headers={"X-API-Key": "invalid-key"}).status_code == 401
    assert client.get("/api/read", headers={"X-API-Key": API_KEY}).status_code == 200
    assert client.get("/api/read", headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200
    read_only = _settings(scopes=["read"])
    read_only_app = FastAPI()

    @read_only_app.middleware("http")
    async def read_only_control(request, call_next):
        """使用只读配置验证写接口拒绝行为。

        参数:
            - `request`：FastAPI 请求上下文。
            - `call_next`：继续调用下游中间件或路由的异步函数。
        """
        return await auth_guard(request, call_next, read_only)

    @read_only_app.post("/api/write")
    async def read_only_write_endpoint():
        """提供受保护的写接口测试入口。"""
        return {"status": "write"}

    read_only_client = TestClient(read_only_app)
    response = read_only_client.post("/api/write", headers={"X-API-Key": API_KEY})
    assert response.status_code == 403


def test_project_access_rejects_unlisted_project() -> None:
    """验证已认证调用方只能访问授权项目。"""
    identity = ApiIdentity(
        subject="reviewer",
        projects=frozenset({"waky3"}),
        scopes=frozenset({"read"}),
    )
    request = SimpleNamespace(state=SimpleNamespace(api_identity=identity))
    require_project_access(request, "waky3")
    with pytest.raises(HTTPException) as exc_info:
        require_project_access(request, "waky4")
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException):
        require_project_access(request, None)


def test_auth_configuration_fails_closed() -> None:
    """验证启用认证但未配置密钥时启动校验失败。"""
    with pytest.raises(ValueError):
        validate_auth_settings(Settings(auth_enabled=True, api_keys_json="{}"))


def test_auth_configuration_rejects_empty_projects_and_unknown_scopes() -> None:
    """验证项目为空或 scope 未知时认证配置失败关闭。"""
    empty_projects = {API_KEY: {"subject": "reviewer", "projects": [], "scopes": ["read"]}}
    with pytest.raises(ValueError):
        validate_auth_settings(
            Settings(auth_enabled=True, api_keys_json=json.dumps(empty_projects))
        )
    unknown_scope = {API_KEY: {"subject": "reviewer", "projects": ["waky3"], "scopes": ["admin"]}}
    with pytest.raises(ValueError):
        validate_auth_settings(
            Settings(auth_enabled=True, api_keys_json=json.dumps(unknown_scope))
        )


def test_code_endpoint_rejects_unlisted_project() -> None:
    """验证真实代码接口在认证模式下拒绝未授权项目。"""
    settings = _settings()
    secured_app = FastAPI()
    secured_app.include_router(code_router)

    @secured_app.middleware("http")
    async def access_control(request, call_next):
        """为代码接口挂载项目访问控制。

        参数:
            - `request`：FastAPI 请求上下文。
            - `call_next`：继续调用下游中间件或路由的异步函数。
        """
        return await auth_guard(request, call_next, settings)

    response = TestClient(secured_app).get(
        "/api/code/waky4/symbols?name=Guild::invite",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 403
