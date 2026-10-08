"""轻量前端页面接口测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_ui_page_exposes_diagnosis_workflow() -> None:
    """验证前端页面包含任务进度、报告和反馈消费入口。"""
    response = TestClient(app).get("/ui")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "开始诊断" in response.text
    assert "/api/diagnoses/" in response.text
    assert "summaryResponse" in response.text
    assert "/api/diagnoses/history" in response.text
    assert "history-more" in response.text
    assert "renderHistory" in response.text
    assert "retry-task" in response.text
    assert "cancel-task" in response.text
    assert "navigator.clipboard.writeText" in response.text
    assert "EventSource" in response.text
    assert "提交反馈" in response.text
