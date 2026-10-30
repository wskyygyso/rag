"""安全拒绝审计持久化和中间件集成测试。"""

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.auth import auth_guard, security_http_exception_handler
from app.code_api import router as code_router
from app.settings import Settings
from app.task_store import insert_security_audit

API_KEY = "security-audit-test-key-length"


class _Pool:
    """记录安全审计 SQL 参数的连接池替身。"""

    def __init__(self):
        """初始化调用列表。"""
        self.calls = []

    async def execute(self, query, *args):
        """捕获审计写入参数。

        参数:
            - `query`：检索或查询文本。
            - `*args`：可变位置参数集合。
        """
        self.calls.append((query, args))


@pytest.mark.asyncio
async def test_security_audit_persists_only_controlled_fields() -> None:
    """验证审计只保存受控摘要并截断过长字段。"""
    pool = _Pool()
    await insert_security_audit(
        pool,
        "AUTHENTICATION",
        401,
        "缺少 API Key",
        "GET",
        "/api/diagnoses/example",
        client_address="127.0.0.1",
        actor=None,
        request_id="request-1",
    )
    assert len(pool.calls) == 1
    assert pool.calls[0][1] == (
        "AUTHENTICATION",
        401,
        "缺少 API Key",
        "GET",
        "/api/diagnoses/example",
        "127.0.0.1",
        None,
        "request-1",
    )
    assert "X-API-Key" not in pool.calls[0][0]


def _read_settings() -> Settings:
    """返回只读权限的认证配置。"""
    config = {
        API_KEY: {
            "subject": "reviewer",
            "projects": ["waky3"],
            "scopes": ["read"],
        }
    }
    return Settings(auth_enabled=True, api_keys_json=json.dumps(config))


def test_auth_middleware_records_missing_key_and_scope_denial(monkeypatch) -> None:
    """验证认证中间件记录 401 和 scope 403，且不保存密钥。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    records = []

    async def fake_audit(pool, event_type, status_code, reason, method, path, **kwargs):
        """捕获安全审计参数。

        参数:
            - `pool`：数据库连接池或测试替身。
            - `event_type`：安全审计事件类型。
            - `status_code`：HTTP 响应状态码。
            - `reason`：安全拒绝原因。
            - `method`：HTTP 请求方法。
            - `path`：文件或请求路径。
            - `**kwargs`：按关键字传入的附加参数集合。
        """
        records.append((event_type, status_code, reason, method, path, kwargs))

    monkeypatch.setattr(auth_module, "insert_security_audit", fake_audit)
    settings = _read_settings()
    test_app = FastAPI()
    test_app.state.db_pool = object()

    @test_app.middleware("http")
    async def access_control(request, call_next):
        """挂载待测认证中间件。

        参数:
            - `request`：FastAPI 请求上下文。
            - `call_next`：继续调用下游中间件或路由的异步函数。
        """
        return await auth_guard(request, call_next, settings)

    @test_app.post("/api/write")
    async def write_endpoint():
        """提供写接口测试入口。"""
        return {"status": "ok"}

    client = TestClient(test_app)
    missing = client.post("/api/write")
    denied = client.post("/api/write", headers={"X-API-Key": API_KEY})
    assert missing.status_code == 401
    assert denied.status_code == 403
    assert [record[1] for record in records] == [401, 403]
    assert all(API_KEY not in str(record) for record in records)


def test_project_denial_is_recorded_by_exception_handler(monkeypatch) -> None:
    """验证项目越权 403 通过统一异常处理器写入审计。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    records = []

    async def fake_audit(pool, event_type, status_code, reason, method, path, **kwargs):
        """捕获项目越权审计。

        参数:
            - `pool`：数据库连接池或测试替身。
            - `event_type`：安全审计事件类型。
            - `status_code`：HTTP 响应状态码。
            - `reason`：安全拒绝原因。
            - `method`：HTTP 请求方法。
            - `path`：文件或请求路径。
            - `**kwargs`：按关键字传入的附加参数集合。
        """
        records.append((event_type, status_code, reason, method, path, kwargs))

    monkeypatch.setattr(auth_module, "insert_security_audit", fake_audit)
    settings = _read_settings()
    test_app = FastAPI()
    test_app.state.db_pool = object()
    test_app.include_router(code_router)
    test_app.add_exception_handler(HTTPException, security_http_exception_handler)

    @test_app.middleware("http")
    async def access_control(request, call_next):
        """挂载认证中间件以验证路由内授权拒绝。

        参数:
            - `request`：FastAPI 请求上下文。
            - `call_next`：继续调用下游中间件或路由的异步函数。
        """
        return await auth_guard(request, call_next, settings)

    response = TestClient(test_app).get(
        "/api/code/waky4/symbols?name=Guild::invite",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 403
    assert records[0][0] == "AUTHORIZATION"
    assert records[0][1] == 403
    assert records[0][5]["actor"] == "reviewer"
