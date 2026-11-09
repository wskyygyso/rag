"""诊断报告 JSON 和 Markdown 导出测试。"""

from fastapi.testclient import TestClient

from app.main import app
from app.reporting import build_assessment, build_evidence_markdown


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
                "retrieval_plan": {
                    "generic_anchor_count": 8,
                    "generic_hit_count": 14,
                    "discovered_queries": ["Guild::invite"],
                    "discovery_sources": [
                        {
                            "query": "Guild::invite",
                            "reason": "命中文件内最近的符号或配置",
                            "file": "Guild.php",
                            "line": 10,
                        }
                    ],
                },
                "evidence": [
                    {
                        "file": "Guild.php",
                        "line": 10,
                        "content": "invite failed",
                        "context": "10: invite failed",
                    }
                ],
                "deterministic_findings": {
                    "conclusion": "公会邀请受开放状态和人数上限限制。",
                    "facts": [
                        {
                            "category": "人数上限",
                            "statement": "公会满员时邀请会被拒绝。",
                            "evidence_refs": ["Guild.php:10"],
                        }
                    ],
                    "uncertainties": [],
                },
                "call_chains": [],
                "validation": {"database": {"status": "SUCCESS"}},
                "warnings": [],
            },
            "error": None,
            "created_at": "2026-09-30T00:00:00+00:00",
            "updated_at": "2026-09-30T00:00:00+00:00",
        }


def test_report_endpoint_supports_json_markdown_and_evidence() -> None:
    """验证报告接口支持页面 JSON、完整报告和证据 Markdown 三种消费方式。"""
    app.state.db_pool = _Pool()
    try:
        client = TestClient(app)
        json_response = client.get("/api/diagnoses/diag_1/report")
        markdown_response = client.get("/api/diagnoses/diag_1/report?format=markdown")
        evidence_response = client.get("/api/diagnoses/diag_1/report?format=evidence")
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
    assert evidence_response.status_code == 200
    assert "# 诊断证据：diag_1" in evidence_response.text
    assert "## 业务限制与源码依据" in evidence_response.text
    assert "### 1. 人数上限" in evidence_response.text
    assert "公会满员时邀请会被拒绝" in evidence_response.text
    assert "### 自动检索计划" in evidence_response.text
    assert "通用业务片段：8 个" in evidence_response.text
    assert "`Guild::invite` ← 命中文件内最近的符号或配置" in evidence_response.text
    assert "10: invite failed" in evidence_response.text
    assert "本次未执行数据库、配置或日志事实验证" not in evidence_response.text
    assert "数据库 查询成功" not in evidence_response.text
    assert evidence_response.headers["content-disposition"].endswith('"diag_1-evidence.md"')


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


def test_evidence_markdown_separates_unverified_runtime_and_ai_content() -> None:
    """验证证据文件不会把未执行验证或缺失 AI 结论写成已确认事实。"""
    task = {
        "task_id": "diag_evidence",
        "project": "waky6",
        "question": "美元熔断有哪些限制？",
        "environment": "development",
        "status": "COMPLETED",
        "created_at": "2026-11-06T00:00:00+00:00",
        "updated_at": "2026-11-06T00:01:00+00:00",
        "result": {
            "deterministic_findings": {
                "conclusion": "美元兑换会直接中止。",
                "facts": [
                    {
                        "category": "美元兑换",
                        "statement": "命中熔断时返回业务错误码 20018。",
                        "evidence_refs": ["CoinsMerchantControl.php:1731"],
                    }
                ],
            },
            "evidence": [
                {
                    "file": "CoinsMerchantControl.php",
                    "line": 1730,
                    "content": "if ($fused) {",
                    "context": "1730: if ($fused) {\n1731: throw new ApiException(20018);",
                    "query": "ERR_USD_EXCHANGE_FUSE_TRIGGERED",
                }
            ],
            "warnings": ["AI 调用超时"],
        },
    }
    task["assessment"] = build_assessment(task["result"])
    document = build_evidence_markdown(task)
    assert "源码确定性分析" in document
    assert "本次未执行数据库、配置或日志事实验证" in document
    assert "未启用或未生成 AI 诊断叙写" in document
    assert "诊断警告：AI 调用超时" in document
    assert "```text" in document
    assert "- `CoinsMerchantControl.php:1731`：throw new ApiException(20018);" in document
    assert "本次证据快照中未包含该行上下文" not in document
