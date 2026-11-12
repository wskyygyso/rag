"""数据库迁移版本、校验漂移和事务执行测试。"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.migrations import MigrationError, apply_migrations, inspect_migrations, load_migrations

ROOT = Path(__file__).resolve().parents[1]


class _Transaction:
    """模拟 asyncpg 迁移事务上下文。"""

    async def __aenter__(self):
        """进入模拟事务。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """退出模拟事务并保留异常传播。

        参数:
            - `exc_type`：事务块抛出的异常类型，正常退出时为 None。
            - `exc`：事务块抛出的异常实例，正常退出时为 None。
            - `traceback`：异常回溯对象，正常退出时为 None。
        """
        return False


class _Connection:
    """记录迁移 SQL 并返回已应用版本的内存连接替身。"""

    def __init__(self, applied=None, table_exists="schema_migrations") -> None:
        """初始化迁移状态和执行记录。

        参数:
            - `applied`：已应用版本的校验和映射。
            - `table_exists`：`to_regclass` 返回值；None 表示尚未创建迁移表。
        """
        self.applied = dict(applied or {})
        self.table_exists = table_exists
        self.executed = []

    def transaction(self):
        """返回模拟事务上下文。"""
        return _Transaction()

    async def execute(self, query, *args):
        """记录迁移 SQL，并模拟迁移版本登记。

        参数:
            - `query`：待执行 SQL 或迁移脚本原文。
            - `*args`：迁移版本的登记参数。
        """
        self.executed.append((query, args))
        if query.strip().startswith("INSERT INTO schema_migrations"):
            self.applied[args[0]] = args[1]
        return "OK"

    async def fetchval(self, query):
        """返回迁移表存在状态。

        参数:
            - `query`：`to_regclass` 查询。
        """
        return self.table_exists

    async def fetch(self, query, *args):
        """返回已记录迁移版本。

        参数:
            - `query`：已应用迁移查询。
            - `*args`：查询附加参数，当前测试不使用。
        """
        return [
            {"version": version, "checksum": checksum}
            for version, checksum in self.applied.items()
        ]


class _Pool:
    """向迁移器提供单个内存连接的连接池替身。"""

    def __init__(self, connection: _Connection) -> None:
        """保存测试连接。

        参数:
            - `connection`：迁移器使用的内存连接。
        """
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        """提供 asyncpg 风格的连接上下文。"""
        yield self.connection


def _write_migrations(directory) -> None:
    """写入连续编号的最小迁移文件。

    参数:
        - `directory`：pytest 临时迁移目录。
    """
    (directory / "001_create_alpha.sql").write_text("CREATE TABLE alpha (id INT);", encoding="utf-8")
    (directory / "002_create_beta.sql").write_text("CREATE TABLE beta (id INT);", encoding="utf-8")


def test_load_migrations_requires_continuous_versions(tmp_path) -> None:
    """验证迁移器拒绝断号和非法文件名，避免生产漏执行脚本。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    _write_migrations(tmp_path)
    migrations = load_migrations(tmp_path)
    assert [item.version for item in migrations] == [
        "001_create_alpha.sql",
        "002_create_beta.sql",
    ]
    (tmp_path / "004_gap.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="连续编号"):
        load_migrations(tmp_path)


def test_repository_migrations_are_ordered_and_named_consistently() -> None:
    """验证正式迁移目录版本连续，防止开发提交遗漏中间版本。"""
    migrations = load_migrations(ROOT / "migrations")
    assert migrations[0].version.startswith("001_")
    assert migrations[-1].version.startswith("025_")
    assert len(migrations) == 25


@pytest.mark.asyncio
async def test_apply_migrations_records_versions_and_checksums(tmp_path) -> None:
    """验证新迁移在事务中执行并登记可追踪校验和。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    _write_migrations(tmp_path)
    connection = _Connection(table_exists=None)
    applied = await apply_migrations(_Pool(connection), tmp_path)
    assert applied == ["001_create_alpha.sql", "002_create_beta.sql"]
    assert set(connection.applied) == set(applied)
    assert any("CREATE TABLE alpha" in query for query, _ in connection.executed)


@pytest.mark.asyncio
async def test_inspect_migrations_reports_pending_and_drift(tmp_path) -> None:
    """验证发布检查能区分待执行迁移和已发布脚本漂移。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    _write_migrations(tmp_path)
    connection = _Connection(applied={"001_create_alpha.sql": "0" * 64})
    result = await inspect_migrations(_Pool(connection), tmp_path)
    assert result["applied"] == ["001_create_alpha.sql"]
    assert result["pending"] == ["002_create_beta.sql"]
    assert result["drifted"] == ["001_create_alpha.sql"]
