"""人工反馈活动别名的规范化、存储与 API 回归测试。

测试只使用构造记录和内存数据库替身，不访问真实仓库、PostgreSQL 或模型。
重点验证：只接受有业务含义的中文叫法、只返回启用状态的别名、目标活动必须存在，
以及数据库异常时诊断读取路径必须降级而不是抛出。
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import knowledge_api
from app.activity_aliases import (
    list_activity_aliases,
    load_approved_activity_aliases,
    normalize_activity_id,
    normalize_alias,
    set_activity_alias_status,
    upsert_activity_alias,
)
from app.domain.diagnosis.activity_kb import ActivityRecord
from app.main import app


def _alias_row(alias: str, activity_id: str, status_value: str = "approved") -> dict:
    """构造一条与数据库返回结构一致的别名记录。

    参数:
        - `alias`：规范化的中文叫法，类型为 `str`。
        - `activity_id`：目标活动目录名，类型为 `str`。
        - `status_value`：记录状态，类型为 `str`；默认启用。

    Returns:
        包含全部审计字段的字典，时间使用固定 UTC 值便于断言序列化结果。
    """
    timestamp = datetime(2026, 11, 15, tzinfo=timezone.utc)
    return {
        "id": 7,
        "project": "waky3",
        "alias": alias,
        "activity_id": activity_id,
        "status": status_value,
        "source": "feedback",
        "evidence": "业务确认",
        "created_by": "reviewer",
        "updated_by": "reviewer",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


class _AliasPool:
    """模拟别名表操作与项目符号索引查询的连接池替身。"""

    def __init__(self, row: dict) -> None:
        """保存默认返回记录并记录收到的 SQL 参数。

        参数:
            - `row`：别名相关 SQL 的返回记录，类型为 `dict`。
        """
        self.row = row
        self.calls: list = []

    async def fetchrow(self, query: str, *args):
        """返回指纹或别名记录，并记录 SQL 与绑定参数。

        参数:
            - `query`：待执行 SQL，类型为 `str`。
            - `*args`：SQL 绑定参数，按数据库占位符顺序排列。
        """
        self.calls.append((query, args))
        if "project_symbol_indexes" in query:
            return {"repository_fingerprint": "f" * 64}
        row = dict(self.row)
        if "UPDATE project_activity_aliases" in query:
            row["status"] = args[2]
            row["updated_by"] = args[3]
        elif "INSERT INTO project_activity_aliases" in query:
            row["alias"] = args[1]
            row["activity_id"] = args[2]
            row["evidence"] = args[3]
            row["created_by"] = args[4]
            row["updated_by"] = args[4]
        return row

    async def fetch(self, query: str, *args):
        """返回别名列表查询结果，并记录 SQL 与绑定参数。

        参数:
            - `query`：待执行 SQL，类型为 `str`。
            - `*args`：SQL 绑定参数，按数据库占位符顺序排列。
        """
        self.calls.append((query, args))
        return [dict(self.row)]


class _BrokenPool:
    """模拟数据库异常，用于验证诊断读取路径必须降级。"""

    async def fetch(self, query: str, *args):
        """无论查询内容如何都抛出连接异常。

        参数:
            - `query`：待执行 SQL，类型为 `str`。
            - `*args`：SQL 绑定参数，类型为可变位置参数。
        """
        raise RuntimeError("database unavailable")


def test_normalize_alias_accepts_business_name() -> None:
    """验证业务叫法通过规范化，标点和空白被清理。

    返回:
        无返回值。断言“鉴定 大师”与带方括号写法都变成紧凑业务叫法。
    """
    assert normalize_alias("鉴定大师") == "鉴定大师"
    assert normalize_alias("【鉴定 大师】") == "鉴定大师"


def test_normalize_alias_rejects_generic_and_unsafe_values() -> None:
    """验证通用问句词和危险片段不能登记为活动别名。

    返回:
        无返回值。断言纯通用词、URL、空白和超长文本都被拒绝。
    """
    assert normalize_alias("活动等级") == ""
    assert normalize_alias("大师") == ""
    assert normalize_alias("https://example.com") == ""
    assert normalize_alias("   ") == ""
    assert normalize_alias("鉴" * 41) == ""


def test_normalize_activity_id_rejects_paths() -> None:
    """验证活动目录名不能包含路径或上跳片段。

    返回:
        无返回值。断言合法目录保留，斜杠、空白和 `..` 被拒绝。
    """
    assert normalize_activity_id(" gemMaster ") == "gemMaster"
    assert normalize_activity_id("Logic/gemMaster") == ""
    assert normalize_activity_id("../config") == ""
    assert normalize_activity_id("gem Master") == ""


@pytest.mark.asyncio
async def test_load_approved_aliases_returns_mapping() -> None:
    """验证已启用别名被转换为映射，非空字段才进入结果。

    返回:
        无返回值。断言别名到活动目录的映射内容与输入一致。
    """
    pool = _AliasPool(_alias_row("鉴定大师", "gemMaster"))
    aliases = await load_approved_activity_aliases(pool, "waky3")
    assert aliases == {"鉴定大师": "gemMaster"}


@pytest.mark.asyncio
async def test_load_approved_aliases_degrades_on_database_error() -> None:
    """验证别名读取失败返回空映射，不阻断诊断。

    返回:
        无返回值。断言异常被降级为空字典。
    """
    assert await load_approved_activity_aliases(_BrokenPool(), "waky3") == {}


@pytest.mark.asyncio
async def test_alias_storage_passes_bound_parameters() -> None:
    """验证登记、列表和状态更新的 SQL 绑定参数符合预期。

    返回:
        无返回值。断言项目、别名、目标活动和操作人按顺序传给数据库。
    """
    pool = _AliasPool(_alias_row("鉴定大师", "gemMaster"))
    created = await upsert_activity_alias(
        pool,
        "waky3",
        "鉴定大师",
        "gemMaster",
        actor="reviewer",
        evidence="业务确认",
    )
    listed = await list_activity_aliases(pool, "waky3", status="approved", limit=10)
    updated = await set_activity_alias_status(
        pool,
        "waky3",
        7,
        "rejected",
        actor="reviewer",
    )
    assert created["alias"] == "鉴定大师"
    assert listed[0]["activity_id"] == "gemMaster"
    assert updated["status"] == "rejected"
    assert pool.calls[0][1][:3] == ("waky3", "鉴定大师", "gemMaster")
    assert pool.calls[1][1] == ("waky3", "approved", 10)
    assert pool.calls[2][1][:3] == ("waky3", 7, "rejected")


def test_alias_api_registers_lists_and_updates(monkeypatch) -> None:
    """验证别名 API 的登记、列表和状态变更闭环。

    参数:
        - `monkeypatch`：pytest 替换工具，用于注入构造的活动知识库。

    返回:
        无返回值。断言登记返回 201、列表可见、停用后状态同步更新。
    """
    records = (
        ActivityRecord(
            activity_id="gemMaster",
            marker="crontab",
            config_file="waky3_miyouactivity/crontab/gemMaster/config.php",
            aliases_zh=("鉴宝专家",),
            level_count=4,
        ),
    )
    monkeypatch.setattr(
        knowledge_api,
        "load_activity_knowledge",
        lambda *args, **kwargs: records,
    )
    app.state.db_pool = _AliasPool(_alias_row("鉴定大师", "gemMaster"))
    try:
        client = TestClient(app)
        created = client.post(
            "/api/knowledge/waky3/activity-aliases",
            json={
                "alias": "鉴定大师",
                "activity_id": "gemMaster",
                "comment": "业务确认",
            },
        )
        listed = client.get("/api/knowledge/waky3/activity-aliases?limit=10")
        updated = client.post(
            "/api/knowledge/waky3/activity-aliases/7/status",
            json={"status": "rejected"},
        )
        candidates = client.get("/api/knowledge/waky3/activity-candidates?q=gem")
    finally:
        app.state.db_pool = None
    assert created.status_code == 201
    assert created.json()["alias"]["alias"] == "鉴定大师"
    assert created.json()["alias"]["created_at"].startswith("2026-11-15T00:00:00")
    assert listed.status_code == 200
    assert listed.json()["aliases"][0]["activity_id"] == "gemMaster"
    assert updated.status_code == 200
    assert updated.json()["alias"]["status"] == "rejected"
    assert candidates.status_code == 200
    assert candidates.json()["candidates"][0]["activity_id"] == "gemMaster"


def test_alias_api_rejects_invalid_alias_and_missing_activity(monkeypatch) -> None:
    """验证非法叫法和不存在的目标活动都被拒绝。

    参数:
        - `monkeypatch`：pytest 替换工具，用于注入构造的活动知识库。

    返回:
        无返回值。断言通用词返回 422，未知目录返回 422 且提示活动数量。
    """
    records = (
        ActivityRecord(
            activity_id="gemMaster",
            marker="crontab",
            config_file="waky3_miyouactivity/crontab/gemMaster/config.php",
            level_count=4,
        ),
    )
    monkeypatch.setattr(
        knowledge_api,
        "load_activity_knowledge",
        lambda *args, **kwargs: records,
    )
    app.state.db_pool = _AliasPool(_alias_row("鉴定大师", "gemMaster"))
    try:
        client = TestClient(app)
        invalid = client.post(
            "/api/knowledge/waky3/activity-aliases",
            json={"alias": "活动等级", "activity_id": "gemMaster"},
        )
        missing = client.post(
            "/api/knowledge/waky3/activity-aliases",
            json={"alias": "鉴定大师", "activity_id": "notHere"},
        )
    finally:
        app.state.db_pool = None
    assert invalid.status_code == 422
    assert missing.status_code == 422
    assert "活动目录不存在" in missing.json()["detail"]


def test_alias_api_requires_activity_knowledge(monkeypatch) -> None:
    """验证活动知识库不可用时拒绝登记，避免写入未校验映射。

    参数:
        - `monkeypatch`：pytest 替换工具，用于模拟知识库为空。

    返回:
        无返回值。断言登记返回 503。
    """
    monkeypatch.setattr(
        knowledge_api,
        "load_activity_knowledge",
        lambda *args, **kwargs: (),
    )
    app.state.db_pool = _AliasPool(_alias_row("鉴定大师", "gemMaster"))
    try:
        client = TestClient(app)
        response = client.post(
            "/api/knowledge/waky3/activity-aliases",
            json={"alias": "鉴定大师", "activity_id": "gemMaster"},
        )
    finally:
        app.state.db_pool = None
    assert response.status_code == 503
