"""数据库、配置和日志验证工具测试。"""

import asyncio
import json
from types import SimpleNamespace

from pathlib import Path

import pytest

from app.validation_tools import (
    ValidationToolError,
    query_database,
    record_validation_audit,
    read_config,
    redact_sensitive,
    search_logs,
    validate_readonly_sql,
)
from app import validation_tools


class _AuditPool:
    """捕获审计 SQL 参数的最小异步连接池替身。"""

    def __init__(self) -> None:
        self.calls = []

    async def execute(self, query: str, *args: object) -> None:
        """记录一次数据库写入调用，供审计脱敏测试断言。"""
        self.calls.append((query, args))


class _DatabaseConnection:
    """模拟 asyncpg 连接，记录查询参数并控制返回或异常。"""

    def __init__(self, rows=None, error=None) -> None:
        self.rows = rows or []
        self.error = error
        self.closed = False
        self.calls = []

    async def fetch(self, sql: str, *params: object, timeout: float) -> list:
        """返回测试记录，或抛出预设数据库异常。"""
        self.calls.append((sql, params, timeout))
        if self.error is not None:
            raise self.error
        return self.rows

    async def close(self) -> None:
        """记录连接释放，验证异常路径不会泄露连接。"""
        self.closed = True


def test_validate_readonly_sql_rejects_writes_and_multiple_statements() -> None:
    """验证写 SQL、多语句和非查询语句都会被拒绝。"""
    assert validate_readonly_sql("SELECT * FROM guild;") == "SELECT * FROM guild"
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("UPDATE guild SET name = 'x'")
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("SELECT 1; SELECT 2")
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("DELETE FROM guild")
    with pytest.raises(ValidationToolError, match="系统表"):
        validate_readonly_sql("SELECT * FROM pg_catalog.pg_tables")


def test_config_and_log_tools_redact_sensitive_values(tmp_path: Path) -> None:
    """验证配置和日志内容读取后会隐藏 Token 和密码。"""
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "config.ini").write_text("token=abc123\nname=waky3\n", encoding="utf-8")
    (project / "app.log").write_text("invite failed token=abc123\nother line\n", encoding="utf-8")
    config = read_config(str(tmp_path), "waky3", "config.ini")
    logs = search_logs(str(tmp_path), "waky3", "app.log", "invite")
    assert "abc123" not in config["content"]
    assert "abc123" not in logs["matches"][0]["content"]
    assert redact_sensitive("password=secret") == "password=***"


def test_validation_tools_reject_invalid_files_and_limits(tmp_path: Path) -> None:
    """验证不存在文件和结果边界会返回可识别的业务异常。"""
    project = tmp_path / "waky3"
    project.mkdir()
    with pytest.raises(ValidationToolError):
        read_config(str(tmp_path), "waky3", "missing.ini")
    with pytest.raises(ValidationToolError):
        search_logs(str(tmp_path), "waky3", "missing.log", "error")
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("")


@pytest.mark.asyncio
async def test_validation_audit_uses_shared_pool_and_redacts_summary() -> None:
    """验证审计写入复用连接池，且不会保存密码和超长摘要。"""
    pool = _AuditPool()
    await record_validation_audit(
        pool,
        "config_read",
        "SUCCESS",
        task_id="diag_1",
        project="waky3",
        request_summary={"file": "config.ini", "password": "secret"},
        result_summary={"content": "x" * 3000},
    )
    assert len(pool.calls) == 1
    args = pool.calls[0][1]
    assert "secret" not in str(args)
    assert len(json.loads(args[5])["content"]) == 2000


@pytest.mark.asyncio
async def test_query_database_redacts_limits_rows_and_closes_connection(monkeypatch) -> None:
    """验证数据库查询会脱敏、限制行数并在成功后关闭连接。"""
    connection = _DatabaseConnection(
        rows=[{"name": "waky3", "token": "abc123"}, {"name": "extra"}]
    )

    async def fake_connect(*args, **kwargs):
        """返回可控的数据库连接替身。"""
        return connection

    monkeypatch.setattr(validation_tools.asyncpg, "connect", fake_connect)
    result = await query_database(
        SimpleNamespace(database_url="postgresql://test"),
        "SELECT * FROM guild",
        max_rows=1,
        max_result_bytes=1000,
    )
    assert result["total"] == 1
    assert result["rows"][0]["token"] == "***"
    assert connection.closed is True
    assert connection.calls[0][0] == "SELECT * FROM guild"


@pytest.mark.asyncio
async def test_query_database_converts_timeout_and_database_errors(monkeypatch) -> None:
    """验证连接超时和数据库错误会转换为受控业务异常并释放连接。"""
    timeout_connection = _DatabaseConnection(error=asyncio.TimeoutError())

    async def fake_timeout_connect(*args, **kwargs):
        """返回会在查询时超时的连接替身。"""
        return timeout_connection

    monkeypatch.setattr(validation_tools.asyncpg, "connect", fake_timeout_connect)
    with pytest.raises(ValidationToolError, match="超时"):
        await query_database(
            SimpleNamespace(database_url="postgresql://test"), "SELECT 1", max_result_bytes=1000
        )
    assert timeout_connection.closed is True

    error_connection = _DatabaseConnection(error=OSError("connection refused"))

    async def fake_error_connect(*args, **kwargs):
        """返回会抛出连接错误的连接替身。"""
        return error_connection

    monkeypatch.setattr(validation_tools.asyncpg, "connect", fake_error_connect)
    with pytest.raises(ValidationToolError, match="数据库查询失败"):
        await query_database(
            SimpleNamespace(database_url="postgresql://test"), "SELECT 1", max_result_bytes=1000
        )
    assert error_connection.closed is True


def test_query_database_rejects_result_size_limit() -> None:
    """验证数据库结果大小参数存在明确边界。"""
    with pytest.raises(ValidationToolError, match="最大结果大小"):
        asyncio.run(
            query_database(SimpleNamespace(database_url="postgresql://test"), "SELECT 1", max_result_bytes=0)
        )
