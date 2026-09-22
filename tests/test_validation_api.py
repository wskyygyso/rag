"""只读验证 API 的参数和安全边界测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_database_validation_api_rejects_write_sql() -> None:
    """验证数据库接口会在连接数据库前拒绝写 SQL。"""
    response = TestClient(app).post(
        "/api/validation/database/query",
        json={"sql": "UPDATE guild SET name = 'x'"},
    )
    assert response.status_code == 400
    assert "只允许执行只读 SQL" in response.json()["detail"]
