"""项目知识库查询、重建与人工反馈别名接口。

知识库内容由索引和诊断证据自动派生，本模块只提供只读查询和按符号表重新校验
的重建入口；人工反馈别名是唯一允许人工登记的链接输入，必须校验项目访问权限、
叫法格式和目标活动真实存在。数据库不可用时返回 503。
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.activity_aliases import (
    VALID_ALIAS_STATUSES,
    list_activity_aliases,
    normalize_activity_id,
    normalize_alias,
    set_activity_alias_status,
    upsert_activity_alias,
)
from app.auth import operation_actor, require_project_access
from app.domain.diagnosis.activity_kb import ActivityRecord, load_activity_knowledge
from app.project_knowledge import (
    load_config_structure,
    rebuild_glossary_from_code_symbols,
)
from app.settings import get_settings

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class ActivityAliasRequest(BaseModel):
    """人工反馈别名登记参数。

    属性说明：
        - `alias`：问题中出现的中文业务叫法，长度 2 至 40，由服务端再次规范化；
        - `activity_id`：目标活动目录名，必须存在于当前项目活动知识库；
        - `comment`：登记说明或反馈依据，最多 2000 字，用于审计；
        - `submitted_by`：开发模式下的回退操作人；启用认证时以认证主体为准。
    """

    alias: str = Field(min_length=2, max_length=40)
    activity_id: str = Field(min_length=1, max_length=100)
    comment: Optional[str] = Field(default=None, max_length=2000)
    submitted_by: Optional[str] = Field(default=None, max_length=100)


class ActivityAliasStatusRequest(BaseModel):
    """人工反馈别名状态变更参数。

    属性说明：
        - `status`：目标状态；`approved` 恢复参与链接，`rejected` 停用但保留记录。
    """

    status: Literal["approved", "rejected"]


async def _activity_records_async(pool, project: str) -> tuple:
    """异步读取项目符号索引指纹并构建活动知识库。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。

    Returns:
        `ActivityRecord` 元组；指纹缺失时按空指纹重建，知识库不可用时为空元组。
    """
    row = await pool.fetchrow(
        "SELECT repository_fingerprint FROM project_symbol_indexes WHERE project = $1",
        project,
    )
    fingerprint = str(row["repository_fingerprint"]) if row is not None else ""
    return load_activity_knowledge(
        get_settings().code_repositories_root,
        project,
        fingerprint,
    )



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


def _candidate_payload(record: ActivityRecord) -> Dict:
    """把活动记录裁剪为前端候选列表需要的安全字段。

    参数:
        - `record`：活动知识库记录，类型为 `ActivityRecord`。

    Returns:
        只包含活动目录、配置路径、中文别名和等级名称的字典；不返回配置正文、
        奖励明细或任何敏感字段，避免候选查询扩大信息暴露面。
    """
    return {
        "activity_id": record.activity_id,
        "config_file": record.config_file,
        "marker": record.marker,
        "level_count": record.level_count,
        "aliases_zh": list(record.aliases_zh[:5]),
        "level_names": list(record.level_names[:5]),
    }


@router.get("/{project}/activity-candidates")
async def activity_candidates(
    project: str,
    request: Request,
    q: str = Query(default="", max_length=40),
    limit: int = Query(default=20, ge=1, le=50),
) -> Dict:
    """按关键字列出可用于别名登记的活动目录候选。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `q`：可选中英文过滤词，类型为 `str`；会同时匹配活动目录和中文别名。
        - `limit`：单次返回条数上限，类型为 `int`。

    Returns:
        候选活动列表，按活动目录排序。知识库不可用时列表为空，前端仍可手动
        输入目录，但登记接口会再次校验目标活动是否存在。
    """
    require_project_access(request, project)
    pool = _pool(request)
    records = await _activity_records_async(pool, project)
    query = str(q or "").strip().casefold()
    matched: List[ActivityRecord] = []
    for record in records:
        if query:
            if query in (record.activity_id or "").casefold():
                matched.append(record)
                continue
            if any(query in alias.casefold() for alias in record.aliases_zh):
                matched.append(record)
            continue
        matched.append(record)
    matched.sort(key=lambda item: item.activity_id)
    return {
        "project": project,
        "total": len(matched),
        "candidates": [_candidate_payload(item) for item in matched[:limit]],
    }


@router.post("/{project}/activity-aliases", status_code=status.HTTP_201_CREATED)
async def create_activity_alias(
    project: str,
    payload: ActivityAliasRequest,
    request: Request,
) -> Dict:
    """登记一条人工反馈别名，并校验目标活动真实存在。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `payload`：别名登记请求体，类型为 `ActivityAliasRequest`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。

    Returns:
        登记后的别名记录，包含状态、操作人和时间。重复登记同一别名时更新目标
        活动并重新启用，审计字段保留最近操作人。

    Raises:
        HTTPException：格式非法返回 422；知识库不可用或数据库失败返回 503。
    """
    require_project_access(request, project)
    pool = _pool(request)
    alias = normalize_alias(payload.alias)
    if not alias:
        raise HTTPException(
            status_code=422,
            detail="别名叫法不合法：需为 2 至 40 字中文业务叫法，且不能只包含通用问句词",
        )
    activity_id = normalize_activity_id(payload.activity_id)
    if not activity_id:
        raise HTTPException(status_code=422, detail="目标活动目录不合法")
    records = await _activity_records_async(pool, project)
    if not records:
        raise HTTPException(
            status_code=503,
            detail="活动知识库不可用，无法校验目标活动",
        )
    if not any(record.activity_id == activity_id for record in records):
        raise HTTPException(
            status_code=422,
            detail=f"活动目录不存在：{activity_id}（当前项目共 {len(records)} 个活动）",
        )
    try:
        record = await upsert_activity_alias(
            pool,
            project,
            alias,
            activity_id,
            actor=operation_actor(request, payload.submitted_by),
            evidence=payload.comment or "",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"别名登记失败: {type(exc).__name__}",
        )
    if not record:
        raise HTTPException(status_code=503, detail="别名登记失败，数据库未返回记录")
    return {"project": project, "alias": _serialize(record)}


@router.get("/{project}/activity-aliases")
async def get_activity_aliases(
    project: str,
    request: Request,
    status_filter: str = Query(default="", alias="status", max_length=16),
    limit: int = Query(default=50, ge=1, le=100),
) -> Dict:
    """列出项目内的人工反馈别名。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。
        - `status_filter`：可选状态过滤，类型为 `str`；只接受 approved 或 rejected。
        - `limit`：单次返回条数上限，类型为 `int`。

    Returns:
        别名记录列表，按最近更新时间倒序排列。

    Raises:
        HTTPException：状态非法返回 422；数据库异常返回 503。
    """
    require_project_access(request, project)
    pool = _pool(request)
    if status_filter and status_filter not in VALID_ALIAS_STATUSES:
        raise HTTPException(status_code=422, detail="别名状态只支持 approved 或 rejected")
    try:
        rows = await list_activity_aliases(
            pool,
            project,
            status=status_filter,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"别名查询失败: {type(exc).__name__}",
        )
    return {"project": project, "aliases": [_serialize(row) for row in rows]}


@router.post("/{project}/activity-aliases/{alias_id}/status")
async def update_activity_alias_status(
    project: str,
    alias_id: int,
    payload: ActivityAliasStatusRequest,
    request: Request,
) -> Dict:
    """启用或停用一条人工反馈别名。

    参数:
        - `project`：项目标识，类型为 `str`；用于阻止跨项目修改。
        - `alias_id`：别名记录主键，类型为 `int`。
        - `payload`：状态变更请求体，类型为 `ActivityAliasStatusRequest`。
        - `request`：携带认证身份和数据库连接池的 FastAPI 请求。

    Returns:
        更新后的别名记录；停用后该别名不再参与活动链接，但记录保留用于审计。

    Raises:
        HTTPException：记录不存在返回 404；数据库异常返回 503。
    """
    require_project_access(request, project)
    pool = _pool(request)
    try:
        record = await set_activity_alias_status(
            pool,
            project,
            alias_id,
            payload.status,
            actor=operation_actor(request, None),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"别名状态更新失败: {type(exc).__name__}",
        )
    if record is None:
        raise HTTPException(status_code=404, detail="别名记录不存在")
    return {"project": project, "alias": _serialize(record)}
