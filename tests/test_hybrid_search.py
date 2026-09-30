"""混合检索工具测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import hybrid_search
from app.code_tools import CodeToolError


class _Pool:
    """返回历史诊断记录的连接池替身。"""

    def __init__(self) -> None:
        self.arguments = None

    async def fetch(self, query, project, keyword, since, limit, offset):
        """返回一条模拟历史记录。"""
        self.arguments = (project, keyword, since, limit, offset)
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
    async def fake_chunks(*args, **kwargs):
        """返回一条全文分块结果。"""
        return [{"source_type": "chunk", "source_id": "Guild.php:1-1", "score": 0.7}]

    monkeypatch.setattr(hybrid_search, "search_document_chunks", fake_chunks)
    pool = _Pool()
    result = await hybrid_search.search_hybrid(
        str(tmp_path), "waky3", "invite", pool, limit=10, history_offset=2, history_days=30
    )
    assert {item["source_type"] for item in result["results"]} == {"code", "symbol", "history", "chunk"}
    assert "secret" not in str(result)
    assert result["results"][0]["score"] == 1.0
    assert result["results"][0]["raw_score"] == 1.0
    assert pool.arguments[0] == "waky3"
    assert pool.arguments[3:] == (10, 2)


@pytest.mark.asyncio
async def test_search_hybrid_rejects_invalid_limits(tmp_path: Path) -> None:
    """验证混合检索关键词和数量边界。"""
    with pytest.raises(CodeToolError):
        await hybrid_search.search_hybrid(str(tmp_path), "waky3", "", _Pool(), limit=10)
    with pytest.raises(CodeToolError):
        await hybrid_search.search_hybrid(str(tmp_path), "waky3", "invite", _Pool(), history_offset=-1)
    with pytest.raises(CodeToolError):
        await hybrid_search.search_hybrid(str(tmp_path), "waky3", "invite", _Pool(), history_days=0)
    with pytest.raises(CodeToolError):
        await hybrid_search.search_hybrid(
            str(tmp_path), "waky3", "invite", _Pool(), source_types=["unknown"]
        )


@pytest.mark.asyncio
async def test_search_hybrid_filters_sources_and_applies_weights(monkeypatch, tmp_path: Path) -> None:
    """验证来源筛选和统一权重排序。"""
    project = tmp_path / "waky3"
    project.mkdir()
    monkeypatch.setattr(hybrid_search, "search_code", lambda *args, **kwargs: {"matches": []})
    monkeypatch.setattr(hybrid_search, "index_repository", lambda *args, **kwargs: {
        "symbols": [{"symbol_name": "Guild::invite", "file": "Guild.php", "line": 1}],
    })
    monkeypatch.setattr(hybrid_search, "search_document_chunks", lambda *args, **kwargs: _chunks())
    pool = _Pool()
    result = await hybrid_search.search_hybrid(
        str(tmp_path), "waky3", "invite", pool, source_types=["symbol", "chunk"]
    )
    assert {item["source_type"] for item in result["results"]} == {"symbol", "chunk"}
    assert result["results"][0]["source_type"] == "symbol"


async def _chunks():
    """返回固定全文分块结果。"""
    return [{"source_type": "chunk", "source_id": "doc:1-2", "score": 0.8}]
