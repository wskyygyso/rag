"""轻量前端页面接口测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_ui_page_exposes_structured_evidence_workflow() -> None:
    """验证前端页面提供结构化结论、逐条证据和证据文件下载闭环。"""
    response = TestClient(app).get("/ui")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "开始诊断" in response.text
    assert "/api/diagnoses/" in response.text
    assert "检索支撑" in response.text
    assert "summaryResponse" in response.text
    assert "/api/diagnoses/history" in response.text
    assert "history-more" in response.text
    assert "renderHistory" in response.text
    assert "retry-task" in response.text
    assert "rerun-task" in response.text
    assert "cancel-task" in response.text
    assert "navigator.clipboard.writeText" in response.text
    assert "history-status" in response.text
    assert "history-request-id" in response.text
    assert "/api/feedback/" in response.text
    assert "loadProjectSummary" in response.text
    assert "ai-quality" in response.text
    assert "/api/ai/" in response.text
    assert "/evaluations" in response.text
    assert "评测趋势" in response.text
    assert "operation-list" in response.text
    assert "loadOperations" in response.text
    assert "X-API-Key" in response.text
    assert "api-key" in response.text
    assert "use-ai" in response.text
    assert "project-list" in response.text
    assert "loadProjects" in response.text
    assert "projectIndexText" in response.text
    assert "syncProjectSelection" in response.text
    assert "/overview" in response.text
    assert "rebuild-index" in response.text
    assert "index-history" in response.text
    assert "loadIndexHistory" in response.text
    assert "pollIndexStatus" in response.text
    assert "startIndexPolling" in response.text
    assert "stopIndexPolling" in response.text
    assert "等待索引 Worker 接收任务" in response.text
    assert "retryIndexRun" in response.text
    assert "task-board" in response.text
    assert "loadTaskBoard" in response.text
    assert "openBoardTask" in response.text
    assert "cancel-selected" in response.text
    assert "retry-selected" in response.text
    assert "board-operation-result" in response.text
    assert "setInterval" in response.text
    assert "response.body.getReader" in response.text
    assert "提交反馈" in response.text
    assert "诊断结论" in response.text
    assert "业务限制" in response.text
    assert "源码证据" in response.text
    assert "下载证据 .md" in response.text
    assert "format=evidence" in response.text
    assert "renderReport" in response.text
    assert "renderFacts" in response.text
    assert "renderEvidenceLibrary" in response.text
    assert "openEvidenceReference" in response.text
    assert "indexEvidenceContext" in response.text
    assert "renderEvidenceContext" in response.text
    assert "AI 补充未完成" in response.text
    assert "evidence-filter" in response.text
    assert "自动发现来源" in response.text
    assert "不依赖预先维护领域映射" in response.text
    assert "retrieval_plan" in response.text
    assert "result-empty" in response.text
    assert "result-view" in response.text
