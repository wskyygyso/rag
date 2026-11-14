"""人工反馈活动别名的规范化、校验与持久化。

模块职责：
- 把授权用户登记的中文业务叫法规范化后写入项目别名表；
- 按项目读取已启用别名映射，供诊断阶段的活动实体链接使用；
- 提供列表与启用/停用操作，所有记录保留操作人和时间用于审计。

边界与安全约束：
- 别名只接受中文业务叫法，拒绝通用问句词、URL、图片路径和翻译键；
- 活动目录名只接受真实目录名允许的字符，不接受路径分隔符和上跳；
- 已启用别名的读取路径遇到数据库异常时返回空映射并记录告警，不中断诊断；
- 本模块不扫描仓库，目标活动是否存在由 API 层用活动知识库校验。
"""

import logging
import re
from typing import Any, Dict, List, Optional

from app.domain.diagnosis.activity_kb import GENERIC_ZH_TERMS

logger = logging.getLogger(__name__)

# 别名最小长度。单字叫法过于宽泛，容易把无关问题绑定到活动。
MIN_ALIAS_LENGTH = 2
# 别名最大长度，与活动知识库中文片段上限保持一致，避免异常长文本写入库。
MAX_ALIAS_LENGTH = 40
# 活动目录名最大长度，按仓库真实目录规模留出余量。
MAX_ACTIVITY_ID_LENGTH = 100
# 别名清洗时保留的字符范围：中英文、数字和常见业务标点，其余一律去掉。
_ALIAS_PUNCTUATION_PATTERN = re.compile(r"[#【】\[\]（）()：:，,。.！!？?\-_/\\|]")
# 活动目录名允许的字符：字母、数字、下划线、点与短横线，禁止路径分隔符。
_ACTIVITY_ID_PATTERN = re.compile(r"[A-Za-z0-9_.\-]+")
# 别名中不允许出现的片段：地址、图片、翻译键或代码片段会扩大敏感信息暴露面。
_ALIAS_FORBIDDEN_FRAGMENTS = (
    "http",
    "://",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    "@",
    "lang_map",
    "<?php",
    "=>",
)
# 返回给调用方的别名记录字段，顺序与 SQL RETURNING 保持一致。
_ALIAS_FIELDS = (
    "id",
    "project",
    "alias",
    "activity_id",
    "status",
    "source",
    "evidence",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)
# 允许的别名状态：approved 参与链接，rejected 保留记录但不参与。
VALID_ALIAS_STATUSES = {"approved", "rejected"}


def normalize_alias(value: str) -> str:
    """把用户登记的叫法规范化为可存储的别名。

    参数:
        - `value`：用户输入的问题中文叫法，类型为 `str`；允许包含空格和常见
          中文标点，不允许 URL、图片路径、邮箱或代码片段。

    Returns:
        规范化后的别名。出现以下任一情况返回空字符串，由调用方拒绝登记：
        值为空、含禁止片段、清洗后不含中文、长度不在 2 至 40 之间、
        去掉通用问句词后剩余中文不足 2 个字。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    folded = text.casefold()
    if any(fragment in folded for fragment in _ALIAS_FORBIDDEN_FRAGMENTS):
        return ""
    cleaned = _ALIAS_PUNCTUATION_PATTERN.sub("", text)
    cleaned = re.sub(r"\s+", "", cleaned)
    if not re.search(r"[\u4e00-\u9fff]", cleaned):
        return ""
    if len(cleaned) < MIN_ALIAS_LENGTH or len(cleaned) > MAX_ALIAS_LENGTH:
        return ""
    # 只有“活动”“等级”“门槛”这类问句结构词，或完全由它们拼成的叫法，
    # 不能作为活动别名；`鉴定大师` 去掉通用词“大师”后仍保留“鉴定”，允许登记。
    remaining = cleaned
    for term in sorted(GENERIC_ZH_TERMS, key=len, reverse=True):
        remaining = remaining.replace(term, "")
    if len(remaining) < MIN_ALIAS_LENGTH:
        return ""
    return cleaned


def normalize_activity_id(value: str) -> str:
    """校验并规范化目标活动目录名。

    参数:
        - `value`：用户输入的仓库活动目录名，类型为 `str`；必须与活动知识库
          中的 `activity_id` 完全一致。

    Returns:
        合法的活动目录名；含路径分隔符、空白、上跳片段、非法字符或超出
        100 字时返回空字符串，避免把路径或注入内容写入别名表。
    """
    text = str(value or "").strip()
    if not text or len(text) > MAX_ACTIVITY_ID_LENGTH:
        return ""
    if not _ACTIVITY_ID_PATTERN.fullmatch(text):
        return ""
    if text.startswith(".") or ".." in text:
        return ""
    return text


def _alias_record(row: Any) -> Dict[str, Any]:
    """把数据库行转换为普通字典。

    参数:
        - `row`：数据库返回的单行记录，类型为 `asyncpg.Record` 或测试替身映射。

    Returns:
        只包含别名审计字段的字典；`None` 输入返回空字典。
    """
    if row is None:
        return {}
    return {field: row[field] for field in _ALIAS_FIELDS}


async def upsert_activity_alias(
    pool,
    project: str,
    alias: str,
    activity_id: str,
    *,
    actor: Optional[str] = None,
    evidence: str = "",
) -> Dict[str, Any]:
    """登记或更新一条人工反馈别名。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：别名所属项目标识，类型为 `str`。
        - `alias`：已经通过 `normalize_alias` 校验的中文叫法，类型为 `str`。
        - `activity_id`：目标活动目录名，类型为 `str`；必须已存在。
        - `actor`：登记操作人，类型为 `Optional[str]`；认证身份优先，开发模式
          可使用请求头回退值。
        - `evidence`：登记说明或反馈依据，类型为 `str`；不保存密钥与敏感信息。

    Returns:
        写入后的记录字典。同一项目内重复登记同一别名时更新目标活动并重新启用，
        保留首次创建时间，同时更新 `updated_by/updated_at` 用于审计。

    Raises:
        Exception：数据库写入失败时向上抛出，由 API 层转换为 503。
    """
    row = await pool.fetchrow(
        """
        INSERT INTO project_activity_aliases
            (project, alias, activity_id, status, source, evidence, created_by, updated_by)
        VALUES ($1, $2, $3, 'approved', 'feedback', $4, $5, $5)
        ON CONFLICT (project, alias) DO UPDATE
        SET activity_id = EXCLUDED.activity_id,
            status = 'approved',
            evidence = EXCLUDED.evidence,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        RETURNING id, project, alias, activity_id, status, source, evidence,
                  created_by, updated_by, created_at, updated_at
        """,
        project,
        alias,
        activity_id,
        str(evidence or "")[:2000] or None,
        actor,
    )
    return _alias_record(row)


async def list_activity_aliases(
    pool,
    project: str,
    *,
    status: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """按项目列出人工反馈别名。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `status`：可选状态过滤，类型为 `str`；空字符串表示全部状态。
        - `limit`：单次返回条数上限，类型为 `int`；调用方应限制在 1 至 100。

    Returns:
        按最近更新时间倒序排列的别名记录列表；数据库异常由调用方处理。
    """
    rows = await pool.fetch(
        """
        SELECT id, project, alias, activity_id, status, source, evidence,
               created_by, updated_by, created_at, updated_at
        FROM project_activity_aliases
        WHERE project = $1
          AND ($2 = '' OR status = $2)
        ORDER BY updated_at DESC, id DESC
        LIMIT $3
        """,
        project,
        status,
        limit,
    )
    return [_alias_record(row) for row in rows]


async def set_activity_alias_status(
    pool,
    project: str,
    alias_id: int,
    status: str,
    *,
    actor: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """启用或停用一条别名记录。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`；用于阻止跨项目修改。
        - `alias_id`：别名记录主键，类型为 `int`。
        - `status`：目标状态，类型为 `str`；只接受 `approved` 或 `rejected`。
        - `actor`：执行变更的操作人，类型为 `Optional[str]`。

    Returns:
        更新后的记录字典；记录不存在或状态非法时返回 `None`，调用方按 404/422
        处理。停用不删除记录，保留审计痕迹。
    """
    if status not in VALID_ALIAS_STATUSES:
        return None
    row = await pool.fetchrow(
        """
        UPDATE project_activity_aliases
        SET status = $3, updated_by = $4, updated_at = NOW()
        WHERE project = $1 AND id = $2
        RETURNING id, project, alias, activity_id, status, source, evidence,
                  created_by, updated_by, created_at, updated_at
        """,
        project,
        alias_id,
        status,
        actor,
    )
    return _alias_record(row) if row is not None else None


async def load_approved_activity_aliases(pool, project: str) -> Dict[str, str]:
    """读取项目内已启用的别名映射。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。

    Returns:
        `别名 → 活动目录` 映射。只包含 `status='approved'` 且字段非空的记录；
        数据库异常时记录告警并返回空字典，诊断继续按源码启发式链接活动。
    """
    try:
        rows = await pool.fetch(
            """
            SELECT alias, activity_id
            FROM project_activity_aliases
            WHERE project = $1 AND status = 'approved'
            ORDER BY updated_at DESC
            """,
            project,
        )
    except Exception as exc:
        logger.warning(
            "activity_alias_load_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return {}
    aliases: Dict[str, str] = {}
    for row in rows:
        alias = str(row["alias"] or "").strip()
        activity_id = str(row["activity_id"] or "").strip()
        if alias and activity_id:
            aliases[alias] = activity_id
    return aliases
