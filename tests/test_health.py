"""FastAPI 基础入口测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_root_endpoint() -> None:
    """验证服务根路径返回运行状态。"""
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
