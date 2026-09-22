"""数据库、配置和日志验证工具测试。"""

import json

from pathlib import Path

import pytest

from app.validation_tools import (
    ValidationToolError,
    record_validation_audit,
    read_config,
    redact_sensitive,
    search_logs,
    validate_readonly_sql,
)


class _AuditPool:
    """捕获审计 SQL 参数的最小异步连接池替身。"""

    def __init__(self) -> None:
        self.calls = []

    async def execute(self, query: str, *args: object) -> None:
        """记录一次数据库写入调用，供审计脱敏测试断言。"""
        self.calls.append((query, args))


def test_validate_readonly_sql_rejects_writes_and_multiple_statements() -> None:
    """验证写 SQL、多语句和非查询语句都会被拒绝。"""
    assert validate_readonly_sql("SELECT * FROM guild;") == "SELECT * FROM guild"
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("UPDATE guild SET name = 'x'")
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("SELECT 1; SELECT 2")
    with pytest.raises(ValidationToolError):
        validate_readonly_sql("DELETE FROM guild")


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
