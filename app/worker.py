from celery import Celery

from app.task_store import create_pool, get_task, insert_evidence, update_task
from app.settings import get_settings
from app.code_tools import CodeToolError, extract_search_queries, read_file, search_code

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
            status="SEARCHING",
            progress=30,
            current_step="正在搜索项目代码",
        )
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        queries = extract_search_queries(current["question"])
        evidence = []
        warnings = []
        for query in queries:
            try:
                search_result = search_code(settings.code_repositories_root, current["project"], query)
                evidence.extend(
                    {"type": "code", "project": current["project"], "query": query, **match}
                    for match in search_result["matches"]
                )
            except CodeToolError as exc:
                warnings.append(str(exc))
        deduplicated = []
        seen = set()
        for item in evidence:
            key = (item.get("file"), item.get("line"), item.get("content"))
            if key not in seen:
                seen.add(key)
                deduplicated.append(item)
        for item in deduplicated:
            try:
                context = read_file(
                    settings.code_repositories_root,
                    current["project"],
                    item["file"],
                    max(1, item["line"] - 2),
                    item["line"] + 2,
                )
                item["line_start"] = context["start_line"]
                item["line_end"] = context["end_line"]
                item["context"] = context["content"]
                await insert_evidence(pool, task_id, {
                    **item,
                    "content": context["content"],
                    "end_line": context["end_line"],
                })
            except CodeToolError as exc:
                warnings.append(str(exc))
        await update_task(
            pool,
            task_id,
            status="REPORTING",
            progress=80,
            current_step="正在生成代码检索报告",
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
                "message": "已完成第一轮代码关键词检索。",
                "queries": queries,
                "evidence": deduplicated,
                "warnings": warnings,
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
