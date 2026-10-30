"""诊断报告 JSON 和 Markdown 导出测试。"""

from fastapi.testclient import TestClient

from app.main import app
from app.reporting import build_assessment


class _Pool:
    """返回固定诊断报告的连接池替身。"""

    async def fetchrow(self, query, task_id):
        """返回可序列化的诊断任务。

        参数:
            - `query`：检索或查询文本。
            - `task_id`：诊断任务 ID。
        """
        if task_id == "missing":
            return None
        return {
            "id": task_id,
            "project": "waky3",
            "question": "检查邀请失败",
            "environment": "development",
            "status": "COMPLETED",
            "progress": 100,
            "current_step": "完成",
            "result": {
                "queries": ["invite"],
                "evidence": [{"file": "Guild.php", "line": 10, "content": "invite failed"}],
                "call_chains": [],
                "validation": {"database": {"status": "SUCCESS"}},
                "warnings": [],
            },
            "error": None,
            "created_at": "2026-09-30T00:00:00+00:00",
            "updated_at": "2026-09-30T00:00:00+00:00",
        }


def test_report_endpoint_supports_json_and_markdown() -> None:
    """验证报告接口支持 JSON 和 Markdown 两种消费方式。"""
    app.state.db_pool = _Pool()
    try:
        client = TestClient(app)
        json_response = client.get("/api/diagnoses/diag_1/report")
        markdown_response = client.get("/api/diagnoses/diag_1/report?format=markdown")
    finally:
        app.state.db_pool = None
    assert json_response.status_code == 200
    assert json_response.json()["task_id"] == "diag_1"
    assert json_response.json()["assessment"]["confidence"]["level"] == "high"
    assert markdown_response.status_code == 200
    assert "# 诊断报告：diag_1" in markdown_response.text
    assert "## 结论" in markdown_response.text
    assert "## 置信度" in markdown_response.text
    assert "## 已确认事实" in markdown_response.text
    assert "## 未验证项" in markdown_response.text
    assert "## 建议下一步" in markdown_response.text
    assert "## 符号证据" in markdown_response.text
    assert "## 配置线索" in markdown_response.text
    assert "## SQL 线索" in markdown_response.text
    assert "## 代码证据" in markdown_response.text
    assert "Guild.php:10 invite failed" in markdown_response.text
    assert markdown_response.headers["content-disposition"].endswith('"diag_1.md"')


def test_report_endpoint_validates_format_and_missing_task() -> None:
    """验证报告接口对不存在任务和非法格式返回受控错误。"""
    app.state.db_pool = _Pool()
    try:
        client = TestClient(app)
        missing_response = client.get("/api/diagnoses/missing/report")
        invalid_response = client.get("/api/diagnoses/diag_1/report?format=html")
    finally:
        app.state.db_pool = None
    assert missing_response.status_code == 404
    assert invalid_response.status_code == 422


def test_assessment_marks_low_confidence_without_evidence() -> None:
    """验证无证据且验证失败时给出低置信度和未验证说明。"""
    assessment = build_assessment(
        {
            "evidence": [],
            "warnings": ["代码搜索超时"],
            "validation": {"database": {"status": "FAILED", "error": "数据库不可用"}},
        }
    )
    assert assessment["confidence"]["level"] == "low"
    assert "未定位到相关代码证据" in assessment["unverified_items"]
    assert any("数据库验证失败" in item for item in assessment["unverified_items"])


def test_assessment_suggests_readonly_steps_from_config_and_sql() -> None:
    """验证配置、SQL 和调用链线索会生成对应排查建议。"""
    assessment = build_assessment(
        {
            "evidence": [{"file": "Guild.php", "line": 8}],
            "configs": [{"key": "anchor.limit", "file": "Guild.php", "line": 7}],
            "sql": [{"operation": "SELECT", "content": "SELECT status FROM guild", "file": "Guild.php", "line": 10}],
            "call_chains": [{"entry": "GuildController::inviteGuild"}],
            "validation": {"database": {"status": "SUCCESS"}},
            "warnings": [],
        }
    )
    suggestions = " ".join(assessment["suggested_next_steps"])
    assert "只读核对配置" in suggestions
    assert "只读 SQL" in suggestions
    assert "调用链" in suggestions
