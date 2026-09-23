"""混合检索工具测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import hybrid_search
from app.code_tools import CodeToolError


class _Pool:
    """返回历史诊断记录的连接池替身。"""

    async def fetch(self, query, project, keyword, limit):
        """返回一条模拟历史记录。"""
        return [
            {
                "id": "diag_old",
                "question": "invite token=secret",
                "status": "COMPLETED",
                "updated_at": SimpleNamespace(isoformat=lambda: "2026-09-23T00:00:00+08:00"),
            }
        ]


@pytest.mark.asyncio
async def test_search_hybrid_merges_sources_and_redacts_history(tmp_path: Path, monkeypatch) -> None:
    """验证混合检索包含代码、符号、历史来源且历史敏感信息已脱敏。"""
    project = tmp_path / "waky3"
    project.mkdir()
    (project / "Guild.php").write_text("class Guild { public function invite() {} }", encoding="utf-8")
    monkeypatch.setattr(
        hybrid_search,
        "search_code",
        lambda *args, **kwargs: {"matches": [{"file": "Guild.php", "line": 1, "content": "invite"}]},
    )
    monkeypatch.setattr(
        hybrid_search,
        "index_repository",
        lambda *args, **kwargs: {
            "symbols": [{"symbol_name": "Guild::invite", "file": "Guild.php", "line": 1}],
            "references": [],
            "configs": [],
            "sql": [],
        },
    )
    result = await hybrid_search.search_hybrid(str(tmp_path), "waky3", "invite", _Pool(), limit=10)
    assert {item["source_type"] for item in result["results"]} == {"code", "symbol", "history"}
    assert "secret" not in str(result)
    assert result["results"][0]["score"] == 1.0


@pytest.mark.asyncio
async def test_search_hybrid_rejects_invalid_limits(tmp_path: Path) -> None:
    """验证混合检索关键词和数量边界。"""
    with pytest.raises(CodeToolError):
        await hybrid_search.search_hybrid(str(tmp_path), "waky3", "", _Pool(), limit=10)
