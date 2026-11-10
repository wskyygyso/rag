"""FastAPI 基础入口测试。"""

from fastapi.testclient import TestClient

from app import main
from app.main import app


def test_root_endpoint_serves_diagnostic_page() -> None:
    """验证服务根路径直接返回可操作的诊断页面。"""
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "开始诊断" in response.text


def test_liveness_endpoint_does_not_check_dependencies(monkeypatch) -> None:
    """验证 liveness 在依赖不可用时仍只反映进程存活。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    async def fail_health(settings):
        """模拟依赖检查失败，该函数不应被 liveness 调用。

        参数:
            - `settings`：应用运行配置。
        """
        raise AssertionError("liveness 不应访问依赖")

    monkeypatch.setattr(main, "get_health", fail_health)
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json()["check"] == "liveness"


def test_readiness_endpoint_returns_503_when_dependency_degraded(monkeypatch) -> None:
    """验证 readiness 在核心依赖降级时返回 503。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    async def degraded_health(settings):
        """返回 Redis 降级的健康结果。

        参数:
            - `settings`：应用运行配置。
        """
        return {
            "status": "degraded",
            "environment": "test",
            "dependencies": {
                "redis": {"status": "error"},
                "postgres": {"status": "ok"},
            },
        }

    monkeypatch.setattr(main, "get_health", degraded_health)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_readiness_endpoint_returns_200_when_dependencies_are_healthy(monkeypatch) -> None:
    """验证 readiness 在核心依赖正常时返回 200。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    async def healthy(settings):
        """返回全部依赖正常的健康结果。

        参数:
            - `settings`：应用运行配置。
        """
        return {
            "status": "ok",
            "environment": "test",
            "dependencies": {
                "redis": {"status": "ok"},
                "postgres": {"status": "ok"},
            },
        }

    monkeypatch.setattr(main, "get_health", healthy)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
