"""生产上线前的配置、依赖和数据库只读边界预检。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
from redis import asyncio as redis_asyncio

from app.auth import validate_auth_settings
from app.settings import Settings

REQUIRED_TABLES = {
    "diagnostic_tasks",
    "diagnostic_evidence",
    "validation_audit",
    "diagnostic_feedback",
    "diagnostic_task_operations",
    "security_audit",
    "document_chunks",
    "document_index_runs",
}


@dataclass
class CheckResult:
    """表示一项预检结果及其可机器读取的附加信息。"""

    name: str
    status: str
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)


def _result(name: str, status: str, message: str, **detail: Any) -> CheckResult:
    """创建预检结果，统一状态值。"""
    return CheckResult(name=name, status=status, message=message, detail=detail)


def static_preflight(settings: Settings, strict: bool) -> List[CheckResult]:
    """检查应用环境、认证、超时、保留期限和数据源配置。"""
    results = []
    environment_status = "PASS" if settings.app_env == "production" else "WARN"
    results.append(
        _result(
            "environment",
            environment_status,
            "运行环境为 production" if environment_status == "PASS" else f"当前环境为 {settings.app_env}",
        )
    )

    if not settings.auth_enabled:
        status = "FAIL" if strict else "WARN"
        results.append(_result("authentication", status, "API Key 认证未启用"))
    else:
        try:
            validate_auth_settings(settings)
            results.append(_result("authentication", "PASS", "API Key 认证配置有效"))
        except ValueError as exc:
            results.append(_result("authentication", "FAIL", str(exc)))

    timeout_pairs = (
        (
            "diagnosis_timeout",
            settings.diagnosis_task_soft_time_limit_seconds,
            settings.diagnosis_task_time_limit_seconds,
        ),
        (
            "index_timeout",
            settings.document_index_soft_time_limit_seconds,
            settings.document_index_time_limit_seconds,
        ),
    )
    for name, soft, hard in timeout_pairs:
        valid = soft >= 1 and hard > soft
        results.append(
            _result(
                name,
                "PASS" if valid else "FAIL",
                f"软超时 {soft}s，硬超时 {hard}s" if valid else "任务超时配置无效",
            )
        )

    retention_values = (
        settings.diagnostic_task_retention_days,
        settings.validation_audit_retention_days,
        settings.security_audit_retention_days,
        settings.document_index_retention_days,
    )
    retention_valid = all(1 <= days <= 3650 for days in retention_values)
    results.append(
        _result(
            "retention",
            "PASS" if retention_valid else "FAIL",
            "数据保留期限有效" if retention_valid else "数据保留期限必须在 1 到 3650 天之间",
        )
    )

    metadata_url = (settings.database_url or "").strip()
    validation_url = (settings.validation_database_url or "").strip()
    results.append(
        _result(
            "metadata_database_url",
            "PASS" if metadata_url else "FAIL",
            "RAgent 元数据库已配置" if metadata_url else "未配置 DATABASE_URL",
        )
    )
    if not validation_url:
        status = "FAIL" if strict else "WARN"
        results.append(_result("validation_database_url", status, "未配置目标只读数据库"))
    elif validation_url == metadata_url:
        results.append(
            _result(
                "validation_database_url",
                "FAIL",
                "目标验证数据库不能复用 RAgent 元数据库",
            )
        )
    else:
        results.append(_result("validation_database_url", "PASS", "目标只读数据库已独立配置"))

    repository_root = Path(settings.code_repositories_root)
    repository_ok = repository_root.is_dir()
    results.append(
        _result(
            "repository_root",
            "PASS" if repository_ok else "FAIL",
            "代码仓库根目录可访问" if repository_ok else "代码仓库根目录不存在",
        )
    )
    return results


async def check_metadata_database(settings: Settings) -> CheckResult:
    """检查 RAgent 元数据库连接和核心表是否齐全。"""
    connection = None
    try:
        connection = await asyncpg.connect(settings.database_url, timeout=3)
        rows = await connection.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
            """,
            list(REQUIRED_TABLES),
        )
        found = {row["table_name"] for row in rows}
        missing = sorted(REQUIRED_TABLES - found)
        if missing:
            return _result("metadata_database", "FAIL", "元数据库缺少核心表", missing=missing)
        return _result("metadata_database", "PASS", "元数据库连接和核心表检查通过")
    except Exception as exc:
        return _result("metadata_database", "FAIL", "无法检查元数据库", error_type=type(exc).__name__)
    finally:
        if connection is not None:
            await connection.close()


async def check_redis(settings: Settings) -> CheckResult:
    """检查验证限流和任务基础设施所需的 Redis 连接。"""
    client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
        return _result("redis", "PASS", "Redis 连接检查通过")
    except Exception as exc:
        return _result("redis", "FAIL", "Redis 连接失败", error_type=type(exc).__name__)
    finally:
        await client.aclose()


async def check_validation_database(settings: Settings) -> Optional[CheckResult]:
    """连接目标数据库并验证 READ ONLY 事务实际生效。"""
    validation_url = (settings.validation_database_url or "").strip()
    if not validation_url or validation_url == (settings.database_url or "").strip():
        return None
    connection = None
    try:
        connection = await asyncpg.connect(validation_url, timeout=3)
        async with connection.transaction(readonly=True):
            transaction_read_only = await connection.fetchval("SHOW transaction_read_only")
        if str(transaction_read_only).lower() != "on":
            return _result("validation_database", "FAIL", "目标数据库 READ ONLY 事务未生效")
        return _result("validation_database", "PASS", "目标数据库连接和 READ ONLY 事务检查通过")
    except Exception as exc:
        return _result(
            "validation_database",
            "FAIL",
            "无法检查目标只读数据库",
            error_type=type(exc).__name__,
        )
    finally:
        if connection is not None:
            await connection.close()


async def run_preflight(settings: Settings, strict: bool) -> List[CheckResult]:
    """执行配置和外部依赖检查，按注册顺序返回全部结果。"""
    results = static_preflight(settings, strict)
    results.append(await check_metadata_database(settings))
    results.append(await check_redis(settings))
    validation_result = await check_validation_database(settings)
    if validation_result is not None:
        results.append(validation_result)
    return results
