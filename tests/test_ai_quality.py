"""AI 诊断质量看板聚合和接口测试。"""

from fastapi.testclient import TestClient

from app.ai_quality import summarize_ai_quality
from app.main import app


def test_ai_quality_aggregates_cache_tokens_and_feedback() -> None:
    """验证 AI 缓存、token、延迟、模型和反馈聚合。

    参数:
        - 无：本测试直接构造固定诊断结果。
    """
    summary = summarize_ai_quality(
        [
            {
                "result": {
                    "ai_metadata": {
                        "model": "ark-code-latest",
                        "protocol": "chat_completions",
                        "cache_hit": True,
                        "latency_ms": 100,
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }
                },
                "correct_feedback": 1,
                "inaccurate_feedback": 0,
            },
            {
                "result": {
                    "ai_metadata": {
                        "model": "ark-code-latest",
                        "protocol": "chat_completions",
                        "cache_hit": False,
                        "latency_ms": 300,
                        "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                    }
                },
                "correct_feedback": 0,
                "inaccurate_feedback": 1,
            },
            {"result": {}, "correct_feedback": 0, "inaccurate_feedback": 0},
        ]
    )
    assert summary["ai_tasks"] == 2
    assert summary["cache_hit_rate"] == 0.5
    assert summary["latency_ms"] == {"avg": 200.0, "max": 300.0}
    assert summary["tokens"] == {"prompt": 30, "completion": 15, "total": 45}
    assert summary["feedback"] == {"correct": 1, "inaccurate": 1}


class _Pool:
    """返回 AI 质量看板聚合行的数据库连接池替身。

    参数:
        - 无：测试替身不保存额外状态。
    """

    async def fetch(self, query, project, days):
        """返回一条带 AI 元数据的模拟任务。

        参数:
            - `query`：AI 质量看板聚合 SQL。
            - `project`：项目筛选值。
            - `days`：最近统计天数。
        """
        return [
            {
                "result": {
                    "ai_metadata": {
                        "model": "model-a",
                        "protocol": "responses",
                        "cache_hit": False,
                        "latency_ms": 50,
                        "usage": {"total_tokens": 12},
                    }
                },
                "correct_feedback": 1,
                "inaccurate_feedback": 0,
            }
        ]


def test_ai_quality_endpoint_returns_project_summary() -> None:
    """验证 AI 质量接口返回项目和最近天数范围。

    参数:
        - 无：测试使用固定连接池替身。
    """
    app.state.db_pool = _Pool()
    try:
        response = TestClient(app).get("/api/ai/waky3/quality?days=7")
    finally:
        app.state.db_pool = None
    assert response.status_code == 200
    assert response.json()["project"] == "waky3"
    assert response.json()["days"] == 7
    assert response.json()["tokens"]["total"] == 12
