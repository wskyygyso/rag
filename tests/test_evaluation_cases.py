"""历史诊断评测候选导出测试。"""

import json

import pytest

from app.evaluation_cases import build_candidate, candidates_from_tasks, fetch_candidates


def test_build_candidate_marks_history_as_pending_review() -> None:
    """验证历史证据转为候选后必须保留待审核状态。"""
    candidate = build_candidate(
        {
            "id": "diag_1",
            "project": "waky3",
            "question": "检查 GuildController::inviteGuild 返回 BD_OP_FORBIDDEN",
            "result": {
                "queries": ["GuildController::inviteGuild", "BD_OP_FORBIDDEN"],
                "evidence": [{"file": "php/action/Guild.php", "line": 8}],
                "symbols": [{"symbol_name": "GuildController::inviteGuild"}],
            },
        }
    )
    assert candidate["id"] == "historical_diag_1"
    assert candidate["review_status"] == "pending"
    assert candidate["expected_files"] == ["php/action/Guild.php"]
    assert candidate["expected_lines"] == [{"file": "php/action/Guild.php", "line": 8}]


def test_candidates_skip_tasks_without_evidence() -> None:
    """验证没有证据的历史任务不会进入评测候选集。"""
    candidates = candidates_from_tasks(
        [{"id": "diag_empty", "project": "waky3", "question": "未知问题", "result": {}}]
    )
    assert candidates == []


class _Pool:
    """模拟历史任务查询连接池。"""

    async def fetch(self, query, project, days, limit):
        """返回一条包含 JSON 字符串结果的任务。"""
        return [
            {
                "id": "diag_2",
                "project": project or "waky3",
                "question": "检查配置",
                "result": json.dumps(
                    {"queries": ["anchor.limit"], "evidence": [{"file": "Guild.php", "line": 7}]}
                ),
            }
        ]


@pytest.mark.asyncio
async def test_fetch_candidates_filters_correct_feedback_in_database_query() -> None:
    """验证历史候选查询只选择正确反馈任务并支持筛选。"""
    candidates = await fetch_candidates(_Pool(), project="waky3", days=30, limit=10)
    assert len(candidates) == 1
    assert candidates[0]["source_task_id"] == "diag_2"
