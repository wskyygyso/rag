"""Celery Worker、任务队列和诊断执行流程。"""

import logging

from billiard.exceptions import SoftTimeLimitExceeded
from celery import Celery

from app.task_store import clear_task_evidence, create_pool, get_task, insert_evidence, update_task
from app.task_store import replace_symbol_index
from app.settings import get_settings
from app.code_tools import CodeToolError, extract_search_queries, read_file, search_code
from app.php_index import index_repository, trace_index_call_chain
from app.llm_diagnosis import count_available_evidence, generate_ai_diagnosis_cached
from app.reporting import build_assessment
from app.symbol_tools import summarize_symbol_index
from app.validation_runner import execute_validation_plan

settings = get_settings()
logger = logging.getLogger(__name__)


def validate_worker_timeouts(current_settings) -> None:
    """校验软超时小于硬超时，避免任务配置互相冲突。

    参数:
        - `current_settings`：待校验的当前 Worker 配置。
    """
    pairs = (
        (
            current_settings.diagnosis_task_soft_time_limit_seconds,
            current_settings.diagnosis_task_time_limit_seconds,
        ),
        (
            current_settings.document_index_soft_time_limit_seconds,
            current_settings.document_index_time_limit_seconds,
        ),
    )
    if any(soft < 1 or hard <= soft for soft, hard in pairs):
        raise ValueError("任务软超时必须为正数且小于硬超时")


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
    """在 Worker 中异步重试指定项目的文档分块索引。

    参数:
        - `project`：项目标识，类型为 `str`。
    """
    import asyncio

    from scripts.index_document_repository import _run

    count = asyncio.run(_run(project))
    return {"project": project, "status": "completed", "chunk_count": count}


async def _execute_diagnosis(task_id: str) -> None:
    """执行单个诊断任务的状态推进、代码检索和证据持久化。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
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
        if current.get("retry_count", 0) > 0:
            await clear_task_evidence(pool, task_id)
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
        symbol_summary = {"symbols": [], "references": [], "configs": [], "sql": []}
        call_chains = []
        try:
            project_index = index_repository(settings.code_repositories_root, current["project"])
            await replace_symbol_index(pool, current["project"], project_index)
            symbol_summary = summarize_symbol_index(project_index, current["project"], limit=100)
            symbol_names = {item["symbol_name"] for item in project_index["symbols"]}
            call_chains = [
                trace_index_call_chain(project_index, query, depth=5)
                for query in queries
                if query in symbol_names
            ]
        except (CodeToolError, ValueError) as exc:
            warnings.append(str(exc))
        await update_task(
            pool,
            task_id,
            status="VERIFYING",
            progress=65,
            current_step="正在执行按需只读验证",
        )
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        validation = await execute_validation_plan(
            settings,
            pool,
            task_id,
            current.get("validation_plan"),
        )
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
        report_result = {
            "message": "已完成第一轮代码关键词检索。",
            "queries": queries,
            "evidence": deduplicated,
            "symbols": symbol_summary["symbols"],
            "references": symbol_summary["references"],
            "configs": symbol_summary["configs"],
            "sql": symbol_summary["sql"],
            "call_chains": call_chains,
            "validation": validation,
            "warnings": warnings,
        }
        if settings.llm_enabled and count_available_evidence(report_result) >= settings.llm_min_evidence_items:
            try:
                ai_run = await generate_ai_diagnosis_cached(
                    settings,
                    current["project"],
                    current["question"],
                    report_result,
                )
                report_result["ai_diagnosis"] = ai_run.diagnosis.model_dump()
                report_result["ai_metadata"] = ai_run.metadata
            except Exception as exc:
                logger.warning(
                    "ai_diagnosis_failed task_id=%s error_type=%s",
                    task_id,
                    type(exc).__name__,
                )
                warnings.append("AI 诊断叙写不可用，已保留确定性证据报告")
        elif settings.llm_enabled:
            warnings.append("代码证据不足，已跳过 AI 诊断叙写")
        report_result["assessment"] = build_assessment(report_result)
        await update_task(
            pool,
            task_id,
            status="COMPLETED",
            progress=100,
            current_step="任务执行器已就绪",
            result=report_result,
        )
    except SoftTimeLimitExceeded as exc:
        await update_task(
            pool,
            task_id,
            status="FAILED",
            progress=100,
            current_step="任务执行超时",
            error="任务超过软超时限制，已停止执行",
        )
        raise
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


@celery_app.task(
    bind=True,
    name="app.worker.run_diagnosis_task",
    queue="diagnosis",
    max_retries=2,
    soft_time_limit=settings.diagnosis_task_soft_time_limit_seconds,
    time_limit=settings.diagnosis_task_time_limit_seconds,
)
def run_diagnosis_task(self, task_id: str) -> dict:
    """Celery 同步入口：运行异步诊断流程并返回任务摘要。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    import asyncio

    asyncio.run(_execute_diagnosis(task_id))
    return {"task_id": task_id, "status": "completed"}
