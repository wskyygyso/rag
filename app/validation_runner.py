"""诊断 Worker 的按需事实验证编排器。"""

from typing import Any, Dict, Optional

import asyncpg

from app.settings import Settings
from app.validation_tools import (
    ValidationToolError,
    query_database,
    read_config,
    record_validation_audit,
    search_logs,
)


def _not_requested() -> Dict[str, str]:
    """返回未配置验证步骤的稳定状态结构。"""
    return {"status": "not_requested"}


async def _audit_failure(
    pool: asyncpg.Pool,
    operation: str,
    task_id: str,
    project: Optional[str],
    error: str,
    request_summary: Dict[str, Any],
) -> None:
    """记录验证失败摘要；审计系统异常不会中断其他验证步骤。"""
    await record_validation_audit(
        pool,
        operation,
        "FAILED",
        task_id=task_id,
        project=project,
        request_summary=request_summary,
        error=error,
    )


async def execute_validation_plan(
    settings: Settings,
    pool: asyncpg.Pool,
    task_id: str,
    plan: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """执行诊断任务携带的只读验证计划，并返回脱敏验证结果。

    单个步骤失败只标记该步骤，不影响其他配置或日志验证；数据库、配置和日志
    工具自身负责结果脱敏，审计记录只保存摘要。
    """
    result: Dict[str, Any] = {
        "database": _not_requested(),
        "configs": [],
        "logs": [],
    }
    if not plan:
        return result

    database = plan.get("database")
    if database:
        project = database.get("project")
        request_summary = {
            "max_rows": database.get("max_rows", 100),
            "max_result_bytes": database.get("max_result_bytes", 512_000),
            "param_count": len(database.get("params") or []),
        }
        try:
            query_result = await query_database(
                settings,
                database["sql"],
                database.get("params"),
                database.get("max_rows", 100),
                max_result_bytes=database.get("max_result_bytes", 512_000),
            )
            await record_validation_audit(
                pool,
                "database_query",
                "SUCCESS",
                task_id=task_id,
                project=project,
                request_summary=request_summary,
                result_summary={"row_count": query_result["total"]},
            )
            result["database"] = {"status": "SUCCESS", **query_result}
        except ValidationToolError as exc:
            await _audit_failure(pool, "database_query", task_id, project, str(exc), request_summary)
            result["database"] = {"status": "FAILED", "error": str(exc)}

    for config in plan.get("configs", []):
        project = config["project"]
        request_summary = {"file": config["file"]}
        try:
            config_result = read_config(settings.code_repositories_root, project, config["file"])
            await record_validation_audit(
                pool,
                "config_read",
                "SUCCESS",
                task_id=task_id,
                project=project,
                request_summary=request_summary,
                result_summary={"content_length": len(config_result["content"])},
            )
            result["configs"].append({"status": "SUCCESS", **config_result})
        except ValidationToolError as exc:
            await _audit_failure(pool, "config_read", task_id, project, str(exc), request_summary)
            result["configs"].append(
                {"status": "FAILED", "project": project, "file": config["file"], "error": str(exc)}
            )

    for log in plan.get("logs", []):
        project = log["project"]
        request_summary = {"file": log["file"], "max_results": log.get("max_results", 100)}
        try:
            log_result = search_logs(
                settings.code_repositories_root,
                project,
                log["file"],
                log["query"],
                log.get("max_results", 100),
            )
            await record_validation_audit(
                pool,
                "logs_search",
                "SUCCESS",
                task_id=task_id,
                project=project,
                request_summary=request_summary,
                result_summary={"match_count": len(log_result["matches"])},
            )
            result["logs"].append({"status": "SUCCESS", **log_result})
        except ValidationToolError as exc:
            await _audit_failure(pool, "logs_search", task_id, project, str(exc), request_summary)
            result["logs"].append(
                {"status": "FAILED", "project": project, "file": log["file"], "error": str(exc)}
            )

    return result
