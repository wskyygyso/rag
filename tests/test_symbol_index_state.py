"""验证诊断任务只在代码快照变化时重写持久化符号索引。"""

import pytest

from app.task_store import replace_symbol_index_if_changed


class _Transaction:
    """提供替换符号索引所需的异步事务上下文。"""

    async def __aenter__(self):
        """进入事务时不执行额外动作。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """退出事务时传播测试代码异常。

        参数:
            - `exc_type`：异常类型；无异常时为 `None`。
            - `exc`：异常实例；无异常时为 `None`。
            - `traceback`：异常堆栈；无异常时为 `None`。
        """
        return False


class _Connection:
    """模拟版本状态查询、项目咨询锁和批量符号写入。"""

    def __init__(self, existing):
        """保存已有索引状态。

        参数:
            - `existing`：模拟的 `project_symbol_indexes` 行；无记录时为 `None`。
        """
        self.existing = existing
        self.execute_calls = []
        self.executemany_calls = []

    def transaction(self):
        """返回异步事务上下文。"""
        return _Transaction()

    async def execute(self, query, *args):
        """记录咨询锁、删除和版本状态写入 SQL。

        参数:
            - `query`：待执行 SQL，类型为 `str`。
            - `*args`：SQL 绑定参数。
        """
        self.execute_calls.append((query, args))

    async def executemany(self, query, args):
        """记录符号批量插入。

        参数:
            - `query`：批量插入 SQL，类型为 `str`。
            - `args`：待插入符号行。
        """
        self.executemany_calls.append((query, list(args)))

    async def fetchrow(self, query, *args):
        """返回预置的旧索引版本。

        参数:
            - `query`：版本状态查询 SQL，类型为 `str`。
            - `*args`：项目标识等绑定参数。
        """
        return self.existing


class _Acquire:
    """为连接池替身提供异步 acquire 上下文。"""

    def __init__(self, connection):
        """保存待返回连接。

        参数:
            - `connection`：模拟数据库连接，类型为 `_Connection`。
        """
        self.connection = connection

    async def __aenter__(self):
        """返回数据库连接。"""
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        """退出连接上下文。

        参数:
            - `exc_type`：异常类型；无异常时为 `None`。
            - `exc`：异常实例；无异常时为 `None`。
            - `traceback`：异常堆栈；无异常时为 `None`。
        """
        return False


class _Pool:
    """只实现版本化符号写入所需 acquire 方法的连接池。"""

    def __init__(self, connection):
        """保存连接替身。

        参数:
            - `connection`：模拟数据库连接，类型为 `_Connection`。
        """
        self.connection = connection

    def acquire(self):
        """返回异步连接上下文。"""
        return _Acquire(self.connection)


def _index() -> dict:
    """生成带一个符号和一条引用的最小索引。"""
    return {
        "symbols": [
            {
                "symbol_type": "class",
                "symbol_name": "FuseRecoveryService",
                "file": "php/FuseRecoveryService.php",
                "line": 3,
                "signature": "class FuseRecoveryService",
            }
        ],
        "references": [{"source": "A::run", "target": "B::stop"}],
        "configs": [],
        "sql": [],
    }


@pytest.mark.asyncio
async def test_matching_snapshot_skips_symbol_rewrite() -> None:
    """验证相同代码快照不会重复删除并写入四万级符号。"""
    connection = _Connection(
        {
            "repository_fingerprint": "c" * 64,
            "symbol_count": 1,
            "reference_count": 1,
            "config_count": 0,
            "sql_count": 0,
        }
    )

    result = await replace_symbol_index_if_changed(
        _Pool(connection), "waky6", "c" * 64, _index()
    )

    assert result == {"updated": False, "symbol_count": 1, "previous_symbol_count": 1}
    assert not any("DELETE FROM code_symbols" in query for query, _ in connection.execute_calls)
    assert connection.executemany_calls == []


@pytest.mark.asyncio
async def test_changed_snapshot_replaces_symbols_and_version_state() -> None:
    """验证代码快照变化后完整替换符号并更新版本状态。"""
    connection = _Connection(
        {
            "repository_fingerprint": "d" * 64,
            "symbol_count": 99,
            "reference_count": 99,
            "config_count": 0,
            "sql_count": 0,
        }
    )

    result = await replace_symbol_index_if_changed(
        _Pool(connection), "waky6", "c" * 64, _index()
    )

    assert result == {"updated": True, "symbol_count": 1, "previous_symbol_count": 99}
    assert any("DELETE FROM code_symbols" in query for query, _ in connection.execute_calls)
    assert len(connection.executemany_calls) == 1
    assert any("INSERT INTO project_symbol_indexes" in query for query, _ in connection.execute_calls)
