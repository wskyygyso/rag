from celery import Celery

from app.task_store import create_pool, get_task, update_task
from app.settings import get_settings

settings = get_settings()
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
)


@celery_app.task(name="app.worker.healthcheck_task")
def healthcheck_task() -> dict:
    return {"status": "ok"}


async def _execute_diagnosis(task_id: str) -> None:
    pool = await create_pool(settings)
    try:
        await update_task(
            pool,
            task_id,
            status="RUNNING",
            progress=10,
            current_step="Worker 已接收任务",
        )
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        await update_task(
            pool,
            task_id,
            status="REPORTING",
            progress=80,
            current_step="正在生成阶段 1 占位报告",
        )
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        await update_task(
            pool,
            task_id,
            status="COMPLETED",
            progress=100,
            current_step="任务执行器已就绪",
            result={
                "message": "异步诊断任务执行器已连接，代码检索能力将在下一阶段接入。",
                "evidence": [],
            },
        )
    except Exception as exc:
        await update_task(
            pool,
            task_id,
            status="FAILED",
            progress=100,
            current_step="任务执行失败",
            error=str(exc),
        )
        raise
    finally:
        await pool.close()


@celery_app.task(bind=True, name="app.worker.run_diagnosis_task", max_retries=2)
def run_diagnosis_task(self, task_id: str) -> dict:
    import asyncio

    asyncio.run(_execute_diagnosis(task_id))
    return {"task_id": task_id, "status": "completed"}
