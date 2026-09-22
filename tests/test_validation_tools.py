"""数据库、配置和日志验证工具测试。"""

from pathlib import Path

import pytest

from app.validation_tools import (
    ValidationToolError,
    read_config,
    redact_sensitive,
    search_logs,
    validate_readonly_sql,
)


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
