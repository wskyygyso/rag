"""只读验证 API 的参数、安全边界和审计行为测试。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app import validation_api
from app.rate_limit import RateLimiter


def test_database_validation_api_rejects_write_sql() -> None:
    """验证数据库接口会在连接数据库前拒绝写 SQL。"""
    response = TestClient(app).post(
        "/api/validation/database/query",
        json={"sql": "UPDATE guild SET name = 'x'"},
    )
    assert response.status_code == 400
    assert "只允许执行只读 SQL" in response.json()["detail"]


def test_validation_api_records_success_audit(monkeypatch, tmp_path: Path) -> None:
    """验证配置读取成功后只记录摘要，不记录完整配置内容。"""
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "config.ini").write_text("token=secret\nname=waky3\n", encoding="utf-8")
    records = []

    async def fake_audit(pool, operation, status, **kwargs):
        """捕获接口发起的审计调用。"""
        records.append((operation, status, kwargs))

    monkeypatch.setattr(validation_api, "record_validation_audit", fake_audit)
    monkeypatch.setattr(validation_api.settings, "code_repositories_root", str(tmp_path))
    app.state.db_pool = object()
    try:
        response = TestClient(app).post(
            "/api/validation/config/read",
            json={"project": "waky3", "file": "config.ini", "task_id": "diag_1"},
        )
    finally:
        app.state.db_pool = None

    assert response.status_code == 200
    assert records[0][0:2] == ("config_read", "SUCCESS")
    assert records[0][2]["result_summary"] == {"content_length": len(response.json()["content"])}
    assert "secret" not in str(records)


def test_validation_api_records_failed_audit(monkeypatch) -> None:
    """验证只读 SQL 被拒绝时仍记录失败审计。"""
    records = []

    async def fake_audit(pool, operation, status, **kwargs):
        """捕获失败审计调用。"""
        records.append((operation, status, kwargs))

    monkeypatch.setattr(validation_api, "record_validation_audit", fake_audit)
    app.state.db_pool = object()
    try:
        response = TestClient(app).post(
            "/api/validation/database/query",
            json={"sql": "UPDATE guild SET name = 'x'", "task_id": "diag_2"},
        )
    finally:
        app.state.db_pool = None

    assert response.status_code == 400
    assert records[0][0:2] == ("database_query", "FAILED")
    assert records[0][2]["task_id"] == "diag_2"


def test_validation_api_returns_429_when_rate_limited(monkeypatch) -> None:
    """验证超出调用方限额时返回 429 和 Retry-After。"""
    monkeypatch.setattr(validation_api, "rate_limiter", RateLimiter(1, 60))
    first = TestClient(app).post(
        "/api/validation/database/query",
        json={"sql": "UPDATE guild SET name = 'x'"},
    )
    second = TestClient(app).post(
        "/api/validation/database/query",
        json={"sql": "SELECT 1"},
    )
    assert first.status_code == 400
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
