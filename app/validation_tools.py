"""数据库、配置和日志只读验证工具。

本模块把外部事实访问限制在只读、白名单和结果大小边界内，供 Agent 或
Worker 在代码分析之后验证候选原因。
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

import asyncpg

from app.code_tools import CodeRepository, CodeToolError
from app.settings import Settings
from app.task_store import insert_validation_audit

WRITE_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|MERGE|CALL|COPY)\b",
    re.IGNORECASE,
)
SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie)\s*([=:])\s*([^\s,;]+)"
)
SENSITIVE_KEY_PATTERN = re.compile(r"(?i)(password|passwd|token|secret|authorization|cookie)")
SYSTEM_SCHEMA_PATTERN = re.compile(r"(?i)\b(pg_catalog|pg_toast|information_schema)\b")


class ValidationToolError(ValueError):
    """验证工具参数非法、查询不安全或目标文件不可访问时的异常。"""


async def record_validation_audit(
    pool: asyncpg.Pool,
    operation: str,
    status: str,
    *,
    task_id: Optional[str] = None,
    project: Optional[str] = None,
    request_summary: Optional[Dict[str, Any]] = None,
    result_summary: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """通过共享连接池写入验证审计记录，并在写入前递归脱敏摘要。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `operation`：操作类型，类型为 `str`。
        - `status`：状态值，类型为 `str`。
        - `task_id`：诊断任务 ID，类型为 `Optional[str]`。
        - `project`：项目标识，类型为 `Optional[str]`。
        - `request_summary`：验证请求脱敏摘要，类型为 `Optional[Dict[str, Any]]`。
        - `result_summary`：验证结果脱敏摘要，类型为 `Optional[Dict[str, Any]]`。
        - `error`：失败原因或数据库异常，类型为 `Optional[str]`。
    """
    try:
        await insert_validation_audit(
            pool,
            operation,
            status,
            task_id=task_id,
            project=project,
            request_summary=_redact_summary(request_summary or {}),
            result_summary=_redact_summary(result_summary or {}),
            error=redact_sensitive(error or ""),
        )
    except Exception:
        # 审计系统故障不能掩盖原始诊断或验证结果。
        return


def _redact_summary(value: Any) -> Any:
    """递归脱敏审计摘要，并限制长文本，避免意外写入密钥或大字段。

    参数:
        - `value`：待递归脱敏和长度限制的验证审计摘要，类型为 `Any`。
    """
    if isinstance(value, str):
        return redact_sensitive(value)[:2000]
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(password|passwd|token|secret|authorization|cookie)", key_text):
                redacted[key_text] = "***"
            else:
                redacted[key_text] = _redact_summary(item)
        return redacted
    if isinstance(value, list):
        return [_redact_summary(item) for item in value[:100]]
    return value


def validate_readonly_sql(sql: str) -> str:
    """校验 SQL 只包含一条只读查询，并返回规范化 SQL。

        允许 `SELECT` 查询和以 `WITH` 开头、最终执行 SELECT 的查询，拒绝写操作、
        多语句和空查询。

    参数:
        - `sql`：待执行或展示的 SQL，类型为 `str`。
    """
    normalized = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql or "", flags=re.S).strip()
    if not normalized:
        raise ValidationToolError("SQL 不能为空")
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if ";" in normalized:
        raise ValidationToolError("不允许执行多条 SQL")
    if SYSTEM_SCHEMA_PATTERN.search(normalized):
        raise ValidationToolError("不允许访问数据库系统表")
    if WRITE_SQL_PATTERN.search(normalized):
        raise ValidationToolError("只允许执行只读 SQL")
    if not re.match(r"(?is)^(SELECT|WITH)\b", normalized):
        raise ValidationToolError("SQL 必须以 SELECT 或 WITH 开头")
    return normalized


def redact_sensitive(value: str) -> str:
    """对日志或数据库结果中的常见敏感键值进行脱敏。

    参数:
        - `value`：待隐藏 Token、密码或 Cookie 的日志或数据库文本，类型为 `str`。
    """
    return SENSITIVE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}***", value)


def _redact_result_value(key: str, value: Any) -> str:
    """按字段名和字段内容共同脱敏数据库结果，防止裸敏感列透出。

    参数:
        - `key`：Redis、配置或字典键，类型为 `str`。
        - `value`：数据库结果中待按字段名脱敏的列值，类型为 `Any`。
    """
    if SENSITIVE_KEY_PATTERN.search(key):
        return "***"
    return redact_sensitive(str(value))


async def query_database(
    settings: Settings,
    sql: str,
    params: Optional[List[Any]] = None,
    max_rows: int = 100,
    timeout_seconds: float = 5,
    max_result_bytes: int = 512_000,
) -> Dict[str, Any]:
    """执行只读数据库查询并返回脱敏结果。

        ``max_rows`` 限制返回记录数，``max_result_bytes`` 限制序列化后的响应大小；
        连接超时、查询超时和数据库异常统一转换为业务异常，避免泄露底层连接细节。

    参数:
        - `settings`：应用运行配置，类型为 `Settings`。
        - `sql`：待执行或展示的 SQL，类型为 `str`。
        - `params`：SQL 绑定参数，类型为 `Optional[List[Any]]`。
        - `max_rows`：最大返回行数，类型为 `int`。
        - `timeout_seconds`：超时时间秒数，类型为 `float`。
        - `max_result_bytes`：结果最大字节数，类型为 `int`。
    """
    if max_rows < 1 or max_rows > 1000:
        raise ValidationToolError("最大返回行数必须在 1 到 1000 之间")
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValidationToolError("查询超时时间必须在 0 到 30 秒之间")
    if max_result_bytes < 1 or max_result_bytes > 5_000_000:
        raise ValidationToolError("最大结果大小必须在 1 到 5000000 字节之间")
    safe_sql = validate_readonly_sql(sql)
    validation_database_url = (settings.validation_database_url or "").strip()
    if not validation_database_url:
        raise ValidationToolError("未配置只读验证数据库")
    connection = None
    try:
        connection = await asyncpg.connect(validation_database_url, timeout=timeout_seconds)
        async with connection.transaction(readonly=True):
            rows = await connection.fetch(
                safe_sql,
                *(params or []),
                timeout=timeout_seconds,
            )
        result_rows = [
            {key: _redact_result_value(str(key), value) for key, value in dict(row).items()}
            for row in rows[:max_rows]
        ]
        serialized_size = len(
            json.dumps(result_rows, ensure_ascii=False, default=str).encode("utf-8")
        )
        if serialized_size > max_result_bytes:
            raise ValidationToolError("数据库查询结果超过大小限制")
        return {"sql": safe_sql, "rows": result_rows, "total": len(result_rows)}
    except asyncio.TimeoutError as exc:
        raise ValidationToolError("数据库查询超时") from exc
    except (asyncpg.PostgresError, OSError) as exc:
        raise ValidationToolError(f"数据库查询失败: {exc}") from exc
    finally:
        if connection is not None:
            await connection.close()


def read_config(
    root: str,
    project: str,
    relative_path: str,
    max_bytes: int = 512_000,
) -> Dict[str, Any]:
    """读取项目配置文件并返回脱敏后的内容。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `relative_path`：项目内相对路径，类型为 `str`。
        - `max_bytes`：最大读取字节数，类型为 `int`。
    """
    try:
        file_path = CodeRepository(root, project).safe_file(relative_path)
    except CodeToolError as exc:
        raise ValidationToolError(str(exc)) from exc
    if file_path.stat().st_size > max_bytes:
        raise ValidationToolError("配置文件超过单次读取大小限制")
    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {
        "project": project,
        "file": relative_path,
        "content": redact_sensitive(content),
    }


def search_logs(
    root: str,
    project: str,
    relative_path: str,
    query: str,
    max_results: int = 100,
) -> Dict[str, Any]:
    """在项目日志文件中按关键词检索并对匹配内容脱敏。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `relative_path`：项目内相对路径，类型为 `str`。
        - `query`：检索或查询文本，类型为 `str`。
        - `max_results`：最大结果数量，类型为 `int`。
    """
    if not query or len(query) > 200:
        raise ValidationToolError("日志关键词不能为空且不能超过 200 个字符")
    if max_results < 1 or max_results > 1000:
        raise ValidationToolError("日志结果数量必须在 1 到 1000 之间")
    try:
        file_path = CodeRepository(root, project).safe_file(relative_path)
    except CodeToolError as exc:
        raise ValidationToolError(str(exc)) from exc
    matches: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        if query.lower() in line.lower():
            matches.append({"file": relative_path, "line": line_number, "content": redact_sensitive(line)})
            if len(matches) >= max_results:
                break
    return {"project": project, "file": relative_path, "query": query, "matches": matches}
