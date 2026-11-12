"""项目知识库抽取、落库、查询和接口回归测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.domain.diagnosis.config_graph import ConfigEntry, ConfigEnvValue
from app.main import app
from app.php_index import parse_php_source
from app.project_knowledge import (
    apply_feedback_to_conclusion,
    extract_glossary,
    load_conclusion_candidates,
    load_glossary_terms,
    question_hash,
    record_config_structure,
    replace_project_glossary_if_changed,
    save_candidate_conclusion,
)
from app.term_expansion import expand_question_terms


class _Transaction:
    """异步事务替身，只提供知识库写入需要的进入和退出语义。"""

    async def __aenter__(self):
        """进入事务并返回自身。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """退出事务；测试不模拟回滚。

        参数:
            - `exc_type`：异常类型，正常退出时为 `None`。
            - `exc`：异常实例，正常退出时为 `None`。
            - `traceback`：异常回溯对象，正常退出时为 `None`。
        """
        return False


class _Connection:
    """记录知识库 SQL 和批量写入的连接替身。"""

    def __init__(self, pool):
        """保存所属连接池，便于写回执行记录。

        参数:
            - `pool`：测试连接池替身。
        """
        self.pool = pool

    async def execute(self, query, *args):
        """记录单条 SQL 并按预设返回下一行。

        参数:
            - `query`：待执行的 SQL 文本。
            - `*args`：SQL 绑定参数。
        """
        self.pool.executes.append((query, args))
        return None

    async def fetchrow(self, query, *args):
        """返回连接池预设的查询结果。

        参数:
            - `query`：待查询的 SQL 文本。
            - `*args`：SQL 绑定参数。
        """
        self.pool.fetchrows.append((query, args))
        return self.pool.next_row

    async def executemany(self, query, rows):
        """记录批量写入语句和参数行。

        参数:
            - `query`：批量写入的 SQL 文本。
            - `rows`：每条记录的参数元组列表。
        """
        self.pool.batches.append((query, list(rows)))
        return None

    def transaction(self):
        """返回事务上下文替身。"""
        return _Transaction()


class _Acquire:
    """提供异步上下文管理语义的连接获取器。"""

    def __init__(self, pool):
        """保存所属连接池。

        参数:
            - `pool`：测试连接池替身。
        """
        self.pool = pool

    async def __aenter__(self):
        """进入连接上下文并返回连接替身。"""
        return _Connection(self.pool)

    async def __aexit__(self, exc_type, exc, traceback):
        """退出连接上下文；测试不模拟真实归还。

        参数:
            - `exc_type`：异常类型，正常退出时为 `None`。
            - `exc`：异常实例，正常退出时为 `None`。
            - `traceback`：异常回溯对象，正常退出时为 `None`。
        """
        return False


class _Pool:
    """支持 acquire 事务和批量写入的知识库连接池替身。"""

    def __init__(self, next_row=None):
        """初始化记录容器和下一次查询结果。

        参数:
            - `next_row`：`fetchrow` 需要返回的字典，类型为 `dict` 或 `None`。
        """
        self.next_row = next_row
        self.executes = []
        self.fetchrows = []
        self.batches = []
        self.fetches = []
        self.rows = []
        self.fetch_value = 0

    def acquire(self):
        """返回带事务能力的异步连接上下文。"""
        return _Acquire(self)

    async def fetch(self, query, *args):
        """记录查询并返回预设行列表。

        参数:
            - `query`：待查询的 SQL 文本。
            - `*args`：SQL 绑定参数。
        """
        self.fetches.append((query, args))
        return self.rows

    async def executemany(self, query, rows):
        """记录直接批量写入的语句和参数行。

        参数:
            - `query`：批量写入的 SQL 文本。
            - `rows`：每条记录的参数元组列表。
        """
        self.batches.append((query, list(rows)))
        return None

    async def fetchrow(self, query, *args):
        """记录查询并返回预设单行。

        参数:
            - `query`：待查询的 SQL 文本。
            - `*args`：SQL 绑定参数。
        """
        self.fetchrows.append((query, args))
        return self.next_row

    async def fetchval(self, query, *args):
        """返回预设标量计数。

        参数:
            - `query`：待查询的 SQL 文本。
            - `*args`：SQL 绑定参数。
        """
        return self.fetch_value

    async def execute(self, query, *args):
        """记录直接执行的 SQL。

        参数:
            - `query`：待执行的 SQL 文本。
            - `*args`：SQL 绑定参数。
        """
        self.executes.append((query, args))
        return None


def test_php_index_extracts_chinese_comment_terms() -> None:
    """验证中文注释能派生类名、方法名和配置键术语。"""
    source = """<?php
// 游戏榜奖励配置
class GameRankingService {
    // 奖池基础值
    public function getPool() {
        return 1;
    }
}
$config = [
    'poolBase' => 900000, // 奖池基础值
];
"""
    index = parse_php_source(source, "crontab/gameRanking/config.php")
    pairs = {(item["term"], item["code_term"]) for item in index["glossary"]}
    assert ("游戏榜奖励配置", "GameRankingService") in pairs
    assert ("奖池基础值", "getPool") in pairs
    assert ("奖池基础值", "poolBase") in pairs


def test_extract_glossary_filters_invalid_and_deduplicates() -> None:
    """验证术语抽取跳过空词、超长词和重复项。"""
    index = {
        "glossary": [
            {"term": " 奖池 ", "code_term": "pool", "term_kind": "config_key"},
            {"term": "奖池", "code_term": "pool", "term_kind": "config_key"},
            {"term": "", "code_term": "pool"},
            {"term": "很长的中文术语" * 10, "code_term": "pool"},
            {"term": "level", "code_term": "pool"},
        ]
    }
    entries = extract_glossary(index)
    assert len(entries) == 1
    assert entries[0].term == "奖池"
    assert entries[0].code_term == "pool"


@pytest.mark.asyncio
async def test_glossary_replacement_skips_same_fingerprint() -> None:
    """验证同指纹同条数时不再重写术语表。"""
    pool = _Pool(next_row={"count": 1})
    index = {
        "glossary": [
            {"term": "奖池", "code_term": "pool", "file": "config.php", "line": 3}
        ]
    }
    result = await replace_project_glossary_if_changed(pool, "waky3", "f" * 64, index)
    assert result["updated"] is False
    assert result["skipped_reason"] == "fingerprint_unchanged"
    assert pool.batches == []


@pytest.mark.asyncio
async def test_glossary_replacement_rewrites_on_change() -> None:
    """验证指纹变化时先删除旧术语再批量写入。"""
    pool = _Pool(next_row={"count": 0})
    index = {
        "glossary": [
            {"term": "奖池", "code_term": "pool", "file": "config.php", "line": 3}
        ]
    }
    result = await replace_project_glossary_if_changed(pool, "waky3", "f" * 64, index)
    assert result["updated"] is True
    assert result["entry_count"] == 1
    assert any("DELETE FROM project_glossary" in query for query, _ in pool.executes)
    assert len(pool.batches) == 1
    assert pool.batches[0][1][0][1] == "奖池"


@pytest.mark.asyncio
async def test_load_glossary_terms_degrades_on_database_error() -> None:
    """验证知识库查询失败时返回 degraded 而不是抛出异常。"""

    class _BrokenPool:
        """模拟数据库故障的连接池。"""

        async def fetch(self, query, *args):
            """抛出连接错误。

            参数:
                - `query`：待查询的 SQL 文本。
                - `*args`：SQL 绑定参数。
            """
            raise RuntimeError("db down")

    result = await load_glossary_terms(_BrokenPool(), "waky3", "奖池基础值")
    assert result["status"] == "degraded"
    assert result["terms"] == []


@pytest.mark.asyncio
async def test_load_glossary_terms_disabled_skips_database() -> None:
    """验证关闭知识库后不访问数据库。"""
    pool = _Pool()
    result = await load_glossary_terms(pool, "waky3", "奖池", enabled=False)
    assert result["status"] == "disabled"
    assert pool.fetches == []


@pytest.mark.asyncio
async def test_config_structure_masks_sensitive_and_address_values() -> None:
    """验证配置结构只保存安全值，敏感键和地址不落明文。"""
    pool = _Pool()
    entries = [
        ConfigEntry(
            file="config/activity.php",
            line=10,
            path=("pool", "base"),
            value_text="900000",
            comment="奖池基础值",
            content="'base' => 900000,",
        ),
        ConfigEntry(
            file="config/pay.php",
            line=20,
            path=("secretKey",),
            value_text="abc123",
            comment="",
            content="'secretKey' => 'abc123',",
        ),
    ]
    env_values = [
        ConfigEnvValue(
            file="config/pay.php",
            line=30,
            path=("notifyUrl",),
            test_value="",
            production_value="https://pay.example.com/callback",
            comment="",
        )
    ]
    count = await record_config_structure(pool, "waky3", "f" * 64, entries, env_values)
    assert count == 4
    rows = pool.batches[0][1]
    values = {(row[3], row[4]): row[5] for row in rows}
    assert values[("base", "numeric")] == "900000"
    assert values[("secretKey", "label")] is None
    assert values[("notifyUrl", "env_production")] is None


@pytest.mark.asyncio
async def test_candidate_conclusion_saved_and_filtered() -> None:
    """验证结论候选落库并只返回未过期且指纹一致的结果。"""
    pool = _Pool()
    saved = await save_candidate_conclusion(
        pool,
        "waky3",
        "f" * 64,
        "task-1",
        "游戏榜前 10 名奖励什么？",
        "1-10 名奖励依次为 100/60/40/20/10 美元。",
        [{"category": "配置枚举", "statement": "1 名 100 美元"}],
        ["config/activity.php:10"],
    )
    assert saved is True
    query, args = pool.executes[-1]
    assert "ON CONFLICT" in query
    assert args[1] == question_hash("游戏榜前 10 名奖励什么？")
    assert args[2] == "游戏榜前 10 名奖励什么？"

    candidate_pool = _Pool()
    candidate_pool.rows = [
        {
            "question": "问题",
            "conclusion": "历史结论",
            "evidence_refs": ["config/a.php:1"],
            "status": "verified",
            "expires_at": datetime(2027, 1, 1, tzinfo=timezone.utc),
        }
    ]
    candidates = await load_conclusion_candidates(
        candidate_pool,
        "waky3",
        "问题",
        "f" * 64,
    )
    assert candidates[0]["conclusion"] == "历史结论"
    query, _ = candidate_pool.fetches[-1]
    assert "repository_fingerprint = $3" in query
    assert "expires_at IS NULL OR expires_at > NOW()" in query


@pytest.mark.asyncio
async def test_feedback_marks_conclusion_verified_or_rejected() -> None:
    """验证正确反馈升级为已验证，不准确反馈标记为拒绝。"""

    class _FeedbackPool:
        """按反馈类型返回结论状态的连接池替身。"""

        def __init__(self, status):
            """保存需要返回的状态。

            参数:
                - `status`：模拟更新后的结论状态。
            """
            self.status = status

        async def fetchrow(self, query, *args):
            """返回预设结论状态。

            参数:
                - `query`：待查询的 SQL 文本。
                - `*args`：SQL 绑定参数。
            """
            return {"status": self.status} if "UPDATE" in query else None

    verified = await apply_feedback_to_conclusion(_FeedbackPool("verified"), "waky3", "问题", "correct")
    rejected = await apply_feedback_to_conclusion(_FeedbackPool("rejected"), "waky3", "问题", "inaccurate")
    assert verified == "verified"
    assert rejected == "rejected"
    assert await apply_feedback_to_conclusion(_FeedbackPool("x"), "waky3", "问题", "escalate") == ""


@pytest.mark.asyncio
async def test_term_expansion_prefers_validated_glossary(monkeypatch) -> None:
    """验证知识库命中时跳过模型扩展，并返回 glossary_hit。

    参数:
        - `monkeypatch`：pytest 提供的属性替换夹具，用于拦截模型调用。
    """
    index = {
        "symbols": [
            {"symbol_name": "GameRankingService", "file": "crontab/gameRanking/config.php"}
        ],
        "configs": [],
        "references": [],
        "sql": [],
    }
    settings = SimpleNamespace(llm_enabled=True, llm_term_expansion_enabled=True)

    async def _fail_request(*args, **kwargs):
        """模型调用不应发生，被调用时直接失败。

        参数:
            - `*args`：原始位置参数，测试中不使用。
            - `**kwargs`：原始关键字参数，测试中不使用。
        """
        raise AssertionError("知识库命中时不应调用模型")

    monkeypatch.setattr("app.term_expansion._request_expansion", _fail_request)
    result = await expand_question_terms(
        "游戏榜奖励",
        project="waky3",
        project_index=index,
        settings=settings,
        glossary_terms=["gameRanking"],
    )
    assert result["status"] == "glossary_hit"
    assert result["terms"] == ["gameRanking"]
    assert result["glossary_term_count"] == 1


def test_knowledge_api_requires_database() -> None:
    """验证数据库未初始化时知识库接口返回 503。"""
    app.state.db_pool = None
    client = TestClient(app)
    assert client.get("/api/knowledge/waky3/summary").status_code == 503
    assert client.post("/api/knowledge/waky3/rebuild").status_code == 503


def test_knowledge_summary_reports_fingerprint_match() -> None:
    """验证知识库摘要返回三类计数和指纹一致状态。"""

    class _SummaryPool:
        """返回固定计数和指纹的接口连接池替身。"""

        async def fetchval(self, query, *args):
            """返回固定条目数。

            参数:
                - `query`：待查询的 SQL 文本。
                - `*args`：SQL 绑定参数。
            """
            return 2

        async def fetchrow(self, query, *args):
            """返回符号索引和术语表指纹。

            参数:
                - `query`：待查询的 SQL 文本。
                - `*args`：SQL 绑定参数。
            """
            if "project_symbol_indexes" in query:
                return {
                    "repository_fingerprint": "f" * 64,
                    "indexed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                }
            return {
                "repository_fingerprint": "f" * 64,
                "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            }

    app.state.db_pool = _SummaryPool()
    try:
        response = TestClient(app).get("/api/knowledge/waky3/summary")
    finally:
        app.state.db_pool = None
    body = response.json()
    assert response.status_code == 200
    assert body["glossary_count"] == 2
    assert body["config_structure_count"] == 2
    assert body["conclusion_count"] == 2
    assert body["fingerprint_matched"] is True
