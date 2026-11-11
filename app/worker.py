"""Celery 应用、队列路由和薄任务适配层。

诊断和索引业务分别委托给工作流与文档索引模块。该模块只负责进程级队列配置、
同步/异步边界和任务返回值，便于在不启动 Celery 时直接测试业务流水线。
"""

import asyncio
from typing import Any

from celery import Celery

from app import diagnosis_workflow
from app.application.diagnosis.pipeline import DiagnosisPipeline
from app.diagnosis_workflow import validate_worker_timeouts
from app.document_index import run_document_index_chunks
from app.settings import get_settings


settings = get_settings()
validate_worker_timeouts(settings)

celery_app = Celery(
    "ragent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_default_queue="diagnosis",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.worker.run_diagnosis_task": {"queue": "diagnosis"},
        "app.worker.run_document_index_task": {"queue": "index"},
    },
)


def __getattr__(name: str) -> Any:
    """兼容访问迁移中的诊断辅助函数。

    参数:
        - `name`：调用方从 `app.worker` 读取但已迁入诊断工作流的属性名。

    Returns:
        诊断工作流中同名对象。该兼容出口只服务测试和旧内部调用，新增生产代码
        应直接从职责模块导入，避免继续扩大 Worker 公开边界。
    """
    return getattr(diagnosis_workflow, name)


@celery_app.task(name="app.worker.healthcheck_task")
def healthcheck_task() -> dict:
    """返回 Worker 可消费任务的最小健康检查结果。"""
    return {"status": "ok"}


@celery_app.task(
    name="app.worker.run_document_index_task",
    queue="index",
    soft_time_limit=settings.document_index_soft_time_limit_seconds,
    time_limit=settings.document_index_time_limit_seconds,
)
def run_document_index_task(project: str) -> dict:
    """在索引队列中重建指定项目的文档分块索引。

    参数:
        - `project`：已经通过 API 项目白名单校验的项目目录名。

    Returns:
        项目名、完成状态和写入分块数。索引模块抛出的异常交给 Celery 标记任务
        失败，不能伪装为成功状态。
    """
    count = asyncio.run(run_document_index_chunks(project))
    return {"project": project, "status": "completed", "chunk_count": count}


@celery_app.task(
    bind=True,
    name="app.worker.run_diagnosis_task",
    queue="diagnosis",
    max_retries=2,
    soft_time_limit=settings.diagnosis_task_soft_time_limit_seconds,
    time_limit=settings.diagnosis_task_time_limit_seconds,
)
def run_diagnosis_task(self, task_id: str) -> dict:
    """运行异步诊断流程并返回任务摘要。

    参数:
        - `task_id`：已持久化的诊断任务 ID。Celery 绑定参数 `self` 由框架传入，
          本函数不使用重试状态；业务失败由工作流写入终态后重新抛出。

    Returns:
        诊断任务 ID 和完成状态。该返回值只表示同步入口已结束，完整报告以数据库
        任务结果为准。
    """
    asyncio.run(DiagnosisPipeline().run(task_id))
    return {"task_id": task_id, "status": "completed"}
