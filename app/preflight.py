"""生产上线前的配置、依赖和数据库只读边界预检。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import asyncpg
from redis import asyncio as redis_asyncio

from app.auth import validate_auth_settings
from app.llm_diagnosis import validate_llm_settings
from app.settings import Settings

REQUIRED_TABLES = {
    "diagnostic_tasks",
    "diagnostic_evidence",
    "code_symbols",
    "validation_audit",
    "diagnostic_feedback",
    "diagnostic_task_operations",
    "security_audit",
    "document_chunks",
    "document_index_runs",
    "ai_evaluation_runs",
    "schema_migrations",
}


@dataclass
class CheckResult:
    """表示一项上线门禁结果，供命令行退出码和发布记录共同使用。

    字段:
        - `name`：稳定检查标识，发布平台和测试以此区分不同门禁。
        - `status`：`PASS`、`WARN` 或 `FAIL`，严格模式会把警告也视为阻断。
        - `message`：面向发布人员的中文结论，不包含密码等敏感值。
        - `detail`：可机器读取的补充信息，例如缺失表名或异常类型。
    """

    name: str
    status: str
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)


def _result(name: str, status: str, message: str, **detail: Any) -> CheckResult:
    """创建一条结构一致的上线检查结论。

    参数:
        - `name`：检查项稳定标识，例如 `metadata_database`，供门禁和发布记录引用。
        - `status`：检查结论，只允许使用 `PASS`、`WARN` 或 `FAIL`。
        - `message`：面向发布人员的中文结论，不能包含连接密码或 API Key。
        - `**detail`：失败上下文或诊断信息，必须可 JSON 序列化。
    """
    return CheckResult(name=name, status=status, message=message, detail=detail)


def _database_endpoint(database_url: str) -> Optional[tuple[str, int, str]]:
    """提取 PostgreSQL 连接地址中的主机、端口和数据库名。

    参数:
        - `database_url`：生产配置中的 PostgreSQL URL；查询参数和账号密码不参与比较。

    返回:
        规范化后的 `(host, port, database)`；地址为空或无法解析时返回 None，由调用方
        输出明确的配置失败，而不是误判为两个独立数据库。
    """
    parsed = urlsplit(database_url)
    try:
        hostname = parsed.hostname
        port = parsed.port or 5432
    except ValueError:
        return None
    if parsed.scheme not in {"postgres", "postgresql"} or not hostname:
        return None
    database = parsed.path.lstrip("/")
    if not database:
        return None
    return hostname.lower(), port, database


async def _postgres_server_identity(connection) -> tuple[str, str, int]:
    """读取 PostgreSQL 当前数据库和服务端监听地址，用于识别地址别名复用。

    参数:
        - `connection`：已认证的 asyncpg 连接；查询只读取当前会话元数据，不修改业务数据。

    返回:
        `(数据库名, 服务端地址, 服务端端口)`；使用 Unix Socket 时地址为空字符串，
        调用方还应结合静态 URL 规范化结果判断是否复用同一数据库。
    """
    row = await connection.fetchrow(
        """
        SELECT
            current_database() AS database_name,
            COALESCE(host(inet_server_addr()), '') AS server_address,
            COALESCE(inet_server_port(), 0) AS server_port
        """
    )
    return str(row["database_name"]), str(row["server_address"]), int(row["server_port"])


def static_preflight(settings: Settings, strict: bool) -> List[CheckResult]:
    """检查应用环境、认证、超时、保留期限和数据源配置。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
        - `strict`：是否启用严格生产门禁，类型为 `bool`。
    """
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

    try:
        validate_llm_settings(settings)
        results.append(
            _result(
                "llm",
                "PASS",
                "LLM 诊断已启用且配置有效" if settings.llm_enabled else "LLM 诊断未启用",
            )
        )
    except ValueError as exc:
        results.append(_result("llm", "FAIL", str(exc)))

    metadata_url = (settings.database_url or "").strip()
    validation_url = (settings.validation_database_url or "").strip()
    metadata_endpoint = _database_endpoint(metadata_url)
    validation_endpoint = _database_endpoint(validation_url)
    results.append(
        _result(
            "metadata_database_url",
            "PASS" if metadata_endpoint else "FAIL",
            "RAgent 元数据库连接地址有效" if metadata_endpoint else "DATABASE_URL 缺失或不是包含数据库名的 PostgreSQL URL",
        )
    )
    if not validation_url:
        status = "FAIL" if strict else "WARN"
        results.append(_result("validation_database_url", status, "未配置目标只读数据库"))
    elif validation_endpoint is None:
        status = "FAIL" if strict else "WARN"
        results.append(
            _result(
                "validation_database_url",
                status,
                "VALIDATION_DATABASE_URL 不是包含数据库名的 PostgreSQL URL",
            )
        )
    elif validation_endpoint == metadata_endpoint:
        results.append(
            _result(
                "validation_database_url",
                "FAIL",
                "目标验证数据库不能复用 RAgent 元数据库",
            )
        )
    else:
        results.append(_result("validation_database_url", "PASS", "目标只读数据库已独立配置"))

    placeholder_values = (
        "<DATABASE_PASSWORD>" in metadata_url,
        "<READONLY_PASSWORD>" in validation_url,
        "<GENERATE_RANDOM_API_KEY>" in (settings.api_keys_json or ""),
    )
    results.append(
        _result(
            "secret_placeholders",
            "FAIL" if any(placeholder_values) else "PASS",
            "生产凭据未包含模板占位值" if not any(placeholder_values) else "生产配置仍包含模板占位值",
        )
    )

    cors_origins = [item.strip() for item in (settings.cors_allowed_origins or "").split(",") if item.strip()]
    cors_valid = "*" not in cors_origins
    results.append(
        _result(
            "cors",
            "PASS" if cors_valid else "FAIL",
            "CORS 白名单未使用通配来源" if cors_valid else "生产环境禁止开放 CORS 通配来源",
        )
    )

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


def enforce_production_settings(settings: Settings) -> None:
    """生产环境关键静态配置不完整时拒绝启动，避免带病上线。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
    """
    if settings.app_env != "production":
        return
    failures = [
        result.name
        for result in static_preflight(settings, strict=True)
        if result.status != "PASS"
    ]
    if failures:
        raise ValueError(f"生产配置预检失败：{', '.join(failures)}")


async def check_metadata_database(settings: Settings) -> CheckResult:
    """检查 RAgent 元数据库连接和核心表是否齐全。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
    """
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
    """检查验证限流和任务基础设施所需的 Redis 连接。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
    """
    client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
        return _result("redis", "PASS", "Redis 连接检查通过")
    except Exception as exc:
        return _result("redis", "FAIL", "Redis 连接失败", error_type=type(exc).__name__)
    finally:
        await client.aclose()


async def check_validation_database(settings: Settings) -> Optional[CheckResult]:
    """连接目标数据库并验证 READ ONLY 事务实际生效。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
    """
    validation_url = (settings.validation_database_url or "").strip()
    if not validation_url or validation_url == (settings.database_url or "").strip():
        return None
    connection = None
    metadata_connection = None
    try:
        connection = await asyncpg.connect(validation_url, timeout=3)
        async with connection.transaction(readonly=True):
            transaction_read_only = await connection.fetchval("SHOW transaction_read_only")
        if str(transaction_read_only).lower() != "on":
            return _result("validation_database", "FAIL", "目标数据库 READ ONLY 事务未生效")
        metadata_connection = await asyncpg.connect(settings.database_url, timeout=3)
        if await _postgres_server_identity(connection) == await _postgres_server_identity(metadata_connection):
            return _result(
                "validation_database",
                "FAIL",
                "目标验证数据库实际指向 RAgent 元数据库，禁止复用",
            )
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
        if metadata_connection is not None:
            await metadata_connection.close()


async def run_preflight(settings: Settings, strict: bool) -> List[CheckResult]:
    """执行配置和外部依赖检查，按注册顺序返回全部结果。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
        - `strict`：是否启用严格生产门禁，类型为 `bool`。
    """
    results = static_preflight(settings, strict)
    results.append(await check_metadata_database(settings))
    results.append(await check_redis(settings))
    validation_result = await check_validation_database(settings)
    if validation_result is not None:
        results.append(validation_result)
    return results
