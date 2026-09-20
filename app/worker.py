from celery import Celery

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

