"""诊断 Worker 按需验证编排器测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import validation_runner
from app.validation_runner import execute_validation_plan


class _Pool:
    """验证编排器使用的最小连接池替身。"""


@pytest.mark.asyncio
async def test_execute_validation_plan_runs_tools_and_records_summaries(
    monkeypatch, tmp_path: Path
) -> None:
    """验证计划会执行数据库、配置和日志步骤，并只记录摘要审计。"""
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "app.ini").write_text("token=secret\n", encoding="utf-8")
    (project / "app.log").write_text("invite failed\n", encoding="utf-8")
    audits = []

    async def fake_query(*args, **kwargs):
        """返回脱敏数据库结果替身。"""
        return {"sql": "SELECT 1", "rows": [{"value": "ok"}], "total": 1}

    async def fake_audit(pool, operation, status, **kwargs):
        """记录验证审计调用。"""
        audits.append((operation, status, kwargs))

    monkeypatch.setattr(validation_runner, "query_database", fake_query)
    monkeypatch.setattr(validation_runner, "record_validation_audit", fake_audit)
    settings = SimpleNamespace(code_repositories_root=str(tmp_path))
    plan = {
        "database": {
            "sql": "SELECT * FROM guild WHERE token = $1",
            "params": ["secret"],
            "max_rows": 5,
            "max_result_bytes": 1000,
            "project": "waky3",
        },
        "configs": [{"project": "waky3", "file": "app.ini"}],
        "logs": [{"project": "waky3", "file": "app.log", "query": "invite"}],
    }

    result = await execute_validation_plan(settings, _Pool(), "diag_1", plan)

    assert result["database"]["status"] == "SUCCESS"
    assert result["configs"][0]["status"] == "SUCCESS"
    assert result["logs"][0]["status"] == "SUCCESS"
    assert [item[:2] for item in audits] == [
        ("database_query", "SUCCESS"),
        ("config_read", "SUCCESS"),
        ("logs_search", "SUCCESS"),
    ]
    assert "secret" not in str(audits)


@pytest.mark.asyncio
async def test_execute_validation_plan_isolates_step_failures(monkeypatch, tmp_path: Path) -> None:
    """验证单个步骤失败时会保留失败状态并继续执行后续步骤。"""
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "app.log").write_text("error happened\n", encoding="utf-8")
    audits = []

    async def fake_query(*args, **kwargs):
        """模拟数据库验证失败。"""
        raise validation_runner.ValidationToolError("数据库查询超时")

    async def fake_audit(pool, operation, status, **kwargs):
        """记录失败审计。"""
        audits.append((operation, status))

    monkeypatch.setattr(validation_runner, "query_database", fake_query)
    monkeypatch.setattr(validation_runner, "record_validation_audit", fake_audit)
    settings = SimpleNamespace(code_repositories_root=str(tmp_path))
    result = await execute_validation_plan(
        settings,
        _Pool(),
        "diag_2",
        {
            "database": {"sql": "SELECT 1"},
            "configs": [{"project": "waky3", "file": "missing.ini"}],
            "logs": [{"project": "waky3", "file": "app.log", "query": "error"}],
        },
    )

    assert result["database"]["status"] == "FAILED"
    assert result["configs"][0]["status"] == "FAILED"
    assert result["logs"][0]["status"] == "SUCCESS"
    assert audits == [("database_query", "FAILED"), ("config_read", "FAILED"), ("logs_search", "SUCCESS")]


def test_validation_plan_model_limits_steps() -> None:
    """验证任务请求模型限制单次配置和日志验证数量。"""
    from pydantic import ValidationError

    from app.validation_plan import ValidationPlan

    with pytest.raises(ValidationError):
        ValidationPlan(configs=[{"project": "waky3", "file": "a.ini"}] * 21)
