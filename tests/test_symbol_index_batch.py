"""验证项目符号索引使用有界批量写入，避免大型仓库逐行写库超时。"""

import pytest

from app.task_store import replace_symbol_index


class _Transaction:
    """为符号索引测试提供异步事务上下文。"""

    async def __aenter__(self):
        """进入事务时不执行额外动作。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """退出事务时传播测试代码抛出的异常。

        参数:
            - `exc_type`：异常类型；无异常时为 `None`。
            - `exc`：异常实例；无异常时为 `None`。
            - `traceback`：异常堆栈对象；无异常时为 `None`。
        """
        return False


class _Connection:
    """记录删除和批量插入调用的数据库连接替身。"""

    def __init__(self):
        """初始化空调用记录。"""
        self.execute_calls = []
        self.executemany_calls = []

    def transaction(self):
        """返回测试用异步事务上下文。"""
        return _Transaction()

    async def execute(self, query, *args):
        """记录单次 SQL 调用。

        参数:
            - `query`：待执行的 SQL 文本，类型为 `str`。
            - `*args`：SQL 绑定参数，类型为可变位置参数。
        """
        self.execute_calls.append((query, args))

    async def executemany(self, query, args):
        """记录批量 SQL 调用及当前批次规模。

        参数:
            - `query`：待批量执行的 SQL 文本，类型为 `str`。
            - `args`：每行绑定参数的集合，类型为可迭代对象。
        """
        self.executemany_calls.append((query, list(args)))


class _Acquire:
    """为连接池测试提供异步 acquire 上下文。"""

    def __init__(self, connection):
        """保存待返回的数据库连接。

        参数:
            - `connection`：记录调用的连接替身，类型为 `_Connection`。
        """
        self.connection = connection

    async def __aenter__(self):
        """返回共享连接替身。"""
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        """退出连接上下文并传播异常。

        参数:
            - `exc_type`：异常类型；无异常时为 `None`。
            - `exc`：异常实例；无异常时为 `None`。
            - `traceback`：异常堆栈对象；无异常时为 `None`。
        """
        return False


class _Pool:
    """只实现符号索引测试所需 acquire 方法的连接池替身。"""

    def __init__(self, connection):
        """保存连接替身。

        参数:
            - `connection`：记录调用的连接替身，类型为 `_Connection`。
        """
        self.connection = connection

    def acquire(self):
        """返回异步连接上下文。"""
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_replace_symbol_index_uses_bounded_executemany_batches() -> None:
    """验证四万级符号不会再触发四万次数据库往返。"""
    connection = _Connection()
    pool = _Pool(connection)
    symbols = [
        {
            "symbol_type": "method",
            "symbol_name": f"ExampleService::method{index}",
            "file": "php/ExampleService.php",
            "line": index + 1,
            "signature": f"method{index}()",
        }
        for index in range(1002)
    ]

    count = await replace_symbol_index(pool, "waky6", {"symbols": symbols, "references": []})

    assert count == 1002
    assert len(connection.execute_calls) == 1
    assert "DELETE FROM code_symbols" in connection.execute_calls[0][0]
    assert [len(call[1]) for call in connection.executemany_calls] == [1000, 2]
    assert all("INSERT INTO code_symbols" in call[0] for call in connection.executemany_calls)
