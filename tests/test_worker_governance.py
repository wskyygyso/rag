"""任务队列、超时和 Worker 中断恢复测试。"""

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from app import worker
from app.settings import Settings
from app.task_store import recover_stale_diagnosis_tasks


class _Pool:
    """记录恢复 SQL 的连接池替身。"""

    def __init__(self, affected: int = 2):
        """初始化受影响行数和调用记录。"""
        self.affected = affected
        self.calls = []

    async def execute(self, query, *args):
        """返回 PostgreSQL 风格更新结果。"""
        self.calls.append((query, args))
        return f"UPDATE {self.affected}"


@pytest.mark.asyncio
async def test_recover_stale_diagnosis_tasks_marks_only_running_states() -> None:
    """验证仅恢复长时间处于运行阶段的任务。"""
    pool = _Pool()
    count = await recover_stale_diagnosis_tasks(pool, 10)
    assert count == 2
    query, args = pool.calls[0]
    assert "RUNNING" in query and "SEARCHING" in query and "REPORTING" in query
    assert args == (10,)


def test_worker_routes_and_time_limits_are_configured() -> None:
    """验证诊断和索引任务进入独立队列并配置软硬超时。"""
    assert worker.celery_app.conf.task_routes["app.worker.run_diagnosis_task"]["queue"] == "diagnosis"
    assert worker.celery_app.conf.task_routes["app.worker.run_document_index_task"]["queue"] == "index"
    assert worker.run_diagnosis_task.soft_time_limit == worker.settings.diagnosis_task_soft_time_limit_seconds
    assert worker.run_diagnosis_task.time_limit == worker.settings.diagnosis_task_time_limit_seconds
    assert worker.run_document_index_task.queue == "index"


def test_worker_rejects_invalid_timeout_pair() -> None:
    """验证软超时不小于硬超时时拒绝 Worker 配置。"""
    invalid = Settings(
        diagnosis_task_soft_time_limit_seconds=100,
        diagnosis_task_time_limit_seconds=100,
    )
    with pytest.raises(ValueError):
        worker.validate_worker_timeouts(invalid)


class _ClosingPool:
    """提供异步关闭方法的数据库池替身。"""

    async def close(self):
        """模拟释放连接池。"""
        return None


@pytest.mark.asyncio
async def test_soft_timeout_marks_diagnosis_failed(monkeypatch) -> None:
    """验证软超时被转换为失败状态并保留超时原因。"""
    updates = []
    pool = _ClosingPool()

    async def fake_create_pool(settings):
        """返回测试连接池。"""
        return pool

    async def fake_update_task(pool, task_id, **kwargs):
        """首次状态推进抛软超时，失败回写正常完成。"""
        updates.append((task_id, kwargs))
        if len(updates) == 1:
            raise SoftTimeLimitExceeded("soft timeout")
        return {}

    monkeypatch.setattr(worker, "create_pool", fake_create_pool)
    monkeypatch.setattr(worker, "update_task", fake_update_task)
    with pytest.raises(SoftTimeLimitExceeded):
        await worker._execute_diagnosis("diag_timeout")
    assert updates[-1][1]["status"] == "FAILED"
    assert updates[-1][1]["current_step"] == "任务执行超时"
