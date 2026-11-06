"""发布后只读烟雾测试脚本逻辑测试。"""

from argparse import Namespace
from contextlib import contextmanager

from scripts import smoke_test


class _TextResponse:
    """模拟 urllib 返回的纯文本 HTTP 响应。"""

    status = 200

    def __init__(self, payload: bytes) -> None:
        """保存烟雾测试需要读取的响应正文。

        参数:
            - `payload`：纯文本度量响应的 UTF-8 字节内容。
        """
        self.payload = payload

    def read(self, limit: int) -> bytes:
        """按 urllib 上限读取响应正文。

        参数:
            - `limit`：调用方允许读取的最大字节数，超限时据此识别异常响应。
        """
        return self.payload[:limit]

    def __enter__(self):
        """进入 urllib 响应上下文并返回自身。"""
        return self

    def __exit__(self, exc_type, exc, traceback):
        """退出响应上下文，不屏蔽调用方异常。

        参数:
            - `exc_type`：上下文块抛出的异常类型，正常退出时为 None。
            - `exc`：上下文块抛出的异常实例，正常退出时为 None。
            - `traceback`：异常回溯对象，正常退出时为 None。
        """
        return False


def test_request_json_preserves_complete_prometheus_text(monkeypatch) -> None:
    """验证度量值位于 200 字节之后时仍能被发布门禁识别。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    payload = ("# HELP ragent_database_up 数据库可用性\n" + "x" * 300 + "\nragent_database_up 1\n").encode()

    def fake_urlopen(request, timeout):
        """返回模拟的 Prometheus 文本响应。

        参数:
            - `request`：烟雾测试构造的只读 urllib 请求。
            - `timeout`：调用方设置的网络超时秒数。
        """
        return _TextResponse(payload)

    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", fake_urlopen)
    status, body = smoke_test.request_json(
        "http://127.0.0.1:8000",
        "/api/metrics",
        "smoke-test-key",
        5.0,
    )
    assert status == 200
    assert "ragent_database_up 1" in body["raw"]


def test_run_smoke_checks_readiness_projects_metrics_and_board(monkeypatch) -> None:
    """验证生产烟雾测试覆盖健康、认证项目、数据库指标和项目看板。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    calls = []

    def fake_request(base_url, path, api_key, timeout):
        """根据只读路径返回稳定测试响应。

        参数:
            - `base_url`：测试 API 根地址。
            - `path`：被检查的只读接口路径。
            - `api_key`：测试 API Key。
            - `timeout`：请求超时秒数。
        """
        calls.append((path, api_key, timeout))
        responses = {
            "/health/ready": (200, {"status": "ok"}),
            "/api/projects": (200, {"projects": [{"name": "waky3"}]}),
            "/api/metrics": (200, {"raw": "ragent_database_up 1\n"}),
            "/api/projects/waky3/task-board": (200, {"project": "waky3"}),
        }
        return responses[path]

    monkeypatch.setattr(smoke_test, "request_json", fake_request)
    monkeypatch.setenv("RAGENT_API_KEY", "smoke-test-key")
    result = smoke_test.run_smoke(
        Namespace(
            base_url="https://ragent.example.com",
            project="waky3",
            require_api=True,
            timeout=5.0,
        )
    )
    assert result["passed"] is True
    assert [item["name"] for item in result["checks"]] == [
        "readiness",
        "authenticated_projects",
        "database_metrics",
        "project_task_board",
    ]
    assert calls[1][1] == "smoke-test-key"


def test_run_smoke_fails_when_database_metrics_are_unavailable(monkeypatch) -> None:
    """验证数据库指标不可用时发布烟雾测试返回失败。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    def fake_request(base_url, path, api_key, timeout):
        """返回数据库降级的指标响应。

        参数:
            - `base_url`：测试 API 根地址。
            - `path`：被检查的只读接口路径。
            - `api_key`：测试 API Key。
            - `timeout`：请求超时秒数。
        """
        if path == "/health/ready":
            return 200, {"status": "ok"}
        if path == "/api/projects":
            return 200, {"projects": []}
        return 200, {"raw": "ragent_database_up 0\n"}

    monkeypatch.setattr(smoke_test, "request_json", fake_request)
    result = smoke_test.run_smoke(
        Namespace(
            base_url="http://127.0.0.1:8000",
            project="",
            require_api=True,
            timeout=5.0,
        )
    )
    assert result["passed"] is False
    assert result["checks"][-1]["name"] == "database_metrics"
