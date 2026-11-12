"""项目知识库查询与重建接口。

知识库内容由索引和诊断证据自动派生，本模块只提供只读查询和按符号表重新校验
的重建入口。所有接口都要求调用方具备对应项目访问权限，数据库不可用时返回 503。
"""

from typing import Dict

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import require_project_access
from app.project_knowledge import (
    load_config_structure,
    rebuild_glossary_from_code_symbols,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _pool(request: Request):
    """返回数据库连接池，未初始化时抛出统一的服务不可用错误。

    参数:
        - `request`：FastAPI 请求上下文，类型为 `Request`。

    Returns:
        `asyncpg.Pool` 风格的连接池。

    Raises:
        HTTPException：连接池尚未初始化时返回 503。
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="数据库暂不可用")
    return pool


def _serialize(row) -> Dict:
    """把数据库行转换为可 JSON 序列化的字典。

    参数:
        - `row`：数据库返回的单行记录，类型为 `asyncpg.Record` 或映射。

    Returns:
        时间字段转为 ISO 字符串后的普通字典。
    """
    item = dict(row)
    for key in ("observed_at", "updated_at", "verified_at", "expires_at", "created_at"):
        value = item.get(key)
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
    return item


@router.get("/{project}/summary")
async def knowledge_summary(project: str, request: Request) -> Dict:
    """返回项目三类知识的条目数和指纹一致性状态。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。

    Returns:
        包含 `glossary_count`、`config_structure_count`、`conclusion_count`、
        符号索引指纹、术语表指纹和 `fingerprint_matched` 的摘要。
    """
    require_project_access(request, project)
    pool = _pool(request)
    glossary_count = await pool.fetchval(
        "SELECT COUNT(*) FROM project_glossary WHERE project = $1", project
    )
    config_count = await pool.fetchval(
        "SELECT COUNT(*) FROM project_config_structure WHERE project = $1", project
    )
    conclusion_count = await pool.fetchval(
        "SELECT COUNT(*) FROM project_verified_conclusions WHERE project = $1", project
    )
    symbol_row = await pool.fetchrow(
        "SELECT repository_fingerprint, indexed_at FROM project_symbol_indexes WHERE project = $1",
        project,
    )
    glossary_row = await pool.fetchrow(
        """
        SELECT repository_fingerprint, MAX(updated_at) AS updated_at
        FROM project_glossary
        WHERE project = $1
        GROUP BY repository_fingerprint
        ORDER BY MAX(updated_at) DESC
        LIMIT 1
        """,
        project,
    )
    symbol_fingerprint = (
        str(symbol_row["repository_fingerprint"]) if symbol_row is not None else ""
    )
    glossary_fingerprint = (
        str(glossary_row["repository_fingerprint"]) if glossary_row is not None else ""
    )
    return {
        "project": project,
        "glossary_count": int(glossary_count or 0),
        "config_structure_count": int(config_count or 0),
        "conclusion_count": int(conclusion_count or 0),
        "symbol_fingerprint": symbol_fingerprint,
        "glossary_fingerprint": glossary_fingerprint,
        "fingerprint_matched": bool(
            symbol_fingerprint
            and glossary_fingerprint
            and symbol_fingerprint == glossary_fingerprint
        ),
        "symbol_indexed_at": (
            symbol_row["indexed_at"].isoformat()
            if symbol_row is not None and hasattr(symbol_row["indexed_at"], "isoformat")
            else None
        ),
        "glossary_updated_at": (
            glossary_row["updated_at"].isoformat()
            if glossary_row is not None and hasattr(glossary_row["updated_at"], "isoformat")
            else None
        ),
    }


@router.get("/{project}/glossary")
async def knowledge_glossary(
    project: str,
    request: Request,
    term: str = Query(default="", max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict:
    """按中文词过滤项目术语表。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `term`：可选中文过滤词，类型为 `str`；空字符串表示不过滤。
        - `limit`：单次返回条数上限，类型为 `int`。

    Returns:
        术语条目列表，包含中文词、代码词、类型、来源、证据引用和置信度。
    """
    require_project_access(request, project)
    pool = _pool(request)
    rows = await pool.fetch(
        """
        SELECT term, code_term, term_kind, source, evidence_ref, confidence,
               repository_fingerprint, updated_at
        FROM project_glossary
        WHERE project = $1
          AND ($2 = '' OR position($2 in term) > 0)
        ORDER BY confidence DESC, LENGTH(term) DESC, updated_at DESC
        LIMIT $3
        """,
        project,
        term,
        limit,
    )
    return {"project": project, "terms": [_serialize(row) for row in rows]}


@router.get("/{project}/config")
async def knowledge_config(
    project: str,
    request: Request,
    file: str = Query(default="", max_length=500),
    key: str = Query(default="", max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict:
    """返回项目配置结构摘要。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `file`：可选配置文件相对路径过滤，类型为 `str`。
        - `key`：可选配置键名过滤，类型为 `str`。
        - `limit`：单次返回条数上限，类型为 `int`。

    Returns:
        配置结构节点列表；敏感字段只显示值类型，不返回明文。
    """
    require_project_access(request, project)
    pool = _pool(request)
    nodes = await load_config_structure(pool, project, file, key, limit)
    return {"project": project, "config_structure": nodes}


@router.get("/{project}/conclusions")
async def knowledge_conclusions(
    project: str,
    request: Request,
    status_filter: str = Query(default="", alias="status", max_length=16),
    limit: int = Query(default=20, ge=1, le=50),
) -> Dict:
    """返回历史结论候选及其状态。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `status_filter`：可选状态过滤，类型为 `str`，取值 candidate、
          verified、expired 或 rejected。
        - `limit`：单次返回条数上限，类型为 `int`。

    Returns:
        结论列表。这些结论只用于排查参考，不会被拼进新的诊断结论。
    """
    require_project_access(request, project)
    pool = _pool(request)
    rows = await pool.fetch(
        """
        SELECT question, conclusion, status, source_task_id, evidence_refs,
               repository_fingerprint, verified_at, expires_at, updated_at
        FROM project_verified_conclusions
        WHERE project = $1
          AND ($2 = '' OR status = $2)
        ORDER BY updated_at DESC
        LIMIT $3
        """,
        project,
        status_filter,
        limit,
    )
    return {
        "project": project,
        "conclusions": [_serialize(row) for row in rows],
        "not_used_in_conclusion": True,
    }


@router.post("/{project}/rebuild")
async def rebuild_knowledge(project: str, request: Request) -> Dict:
    """按现有符号表重新校验项目术语表。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。

    Returns:
        重建结果。该入口不扫描仓库，只删除已不存在的代码词并把剩余术语更新到
        当前符号指纹；新注释派生词会在下一次诊断索引时自动补齐。

    Raises:
        HTTPException：数据库异常返回 503。
    """
    require_project_access(request, project)
    pool = _pool(request)
    try:
        result = await rebuild_glossary_from_code_symbols(pool, project)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"知识库重建失败: {type(exc).__name__}")
    return {"project": project, **result}
