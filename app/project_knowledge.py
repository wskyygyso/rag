"""项目知识库的抽取、落库、指纹失效与查询。

本模块把索引和诊断证据派生成三类可重建知识：中文术语表、配置结构摘要和历史
结论候选。知识库只服务检索与术语扩展，不参与确定性结论拼接，因此任何数据库
异常都必须降级为空结果，不能中断诊断主流程。

边界与安全约束：
- 只接受索引和 `config_graph` 已经过滤的字段，不读取仓库正文之外的业务数据；
- 配置结构只保存数字、布尔、代码常量和普通标识符，敏感键与地址类值不落库；
- 历史结论只作为候选提示返回，调用方不得把它拼进新的结论；
- 所有条目绑定代码快照指纹，代码变化后必须重建或失效。
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 中文术语最大长度，与索引侧抽取保持一致，避免异常注释撑大知识库。
MAX_TERM_LENGTH = 40
# 单次批量写入上限，避免一次诊断把超大索引全部写入数据库。
MAX_KNOWLEDGE_BATCH = 500
# 结论候选默认有效期，单位天。过期后不再参与检索提示。
CONCLUSION_TTL_DAYS = 90
# 敏感字段判定：命中这些键名时只记录结构，不保存任何值。
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:secret|token|password|passwd|private|credential|sign|accesskey|publickey)",
    re.IGNORECASE,
)
# 普通业务标识符允许落库展示；URL、路径、邮箱和含空格文本一律不保存。
_SAFE_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
# 数字、布尔和代码常量用于区分配置值类型。
_NUMERIC_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
_CONSTANT_PATTERN = re.compile(r"\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class GlossaryEntry:
    """描述一条中文业务词到真实代码标识的映射。

    属性说明：
    - `term`：中文业务词，来自注释或运行时问题；
    - `code_term`：仓库中真实存在的代码标识；
    - `term_kind`：`symbol`、`file`、`directory`、`config_key` 或 `runtime`；
    - `source`：`comment`、`symbol`、`path`、`config_key` 或 `llm`；
    - `evidence_ref`：`文件:行号` 或相对路径；
    - `confidence`：0 到 1 的可信度，注释派生高于模型沉淀。
    """

    term: str
    code_term: str
    term_kind: str
    source: str
    evidence_ref: str
    confidence: float

    def to_row(self) -> Dict[str, Any]:
        """返回可直接绑定数据库参数的字段字典。"""
        return {
            "term": self.term,
            "code_term": self.code_term,
            "term_kind": self.term_kind,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
            "confidence": self.confidence,
        }


def question_hash(question: str) -> str:
    """把用户问题规范化为稳定哈希，用于历史结论去重和匹配。

    参数:
        - `question`：用户提交的原始问题，类型为 `str`。

    Returns:
        SHA-256 十六进制摘要。规范化只做去首尾空白、压缩连续空白和转小写，
        不改变语义，保证同一问题重复诊断时命中同一条结论。
    """
    normalized = re.sub(r"\s+", " ", str(question or "").strip()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_glossary(
    index: Dict[str, Any],
    project: str = "",
    limit: int = MAX_KNOWLEDGE_BATCH,
) -> List[GlossaryEntry]:
    """从符号索引的 `glossary` 分组提取合格术语。

    参数:
        - `index`：项目符号索引，类型为 `Dict[str, Any]`；只读取 `glossary`
          列表，不读取源码内容。
        - `project`：项目标识，类型为 `str`；当前仅用于日志上下文。
        - `limit`：最多返回条数，类型为 `int`，默认 500。

    Returns:
        去重后的术语条目。非法类型、空中文词、空代码词和超出长度上限的条目
        会被跳过，不会写入知识库。
    """
    entries: List[GlossaryEntry] = []
    seen = set()
    for item in index.get("glossary") or []:
        if not isinstance(item, dict):
            continue
        term = re.sub(r"\s+", "", str(item.get("term") or "").strip())
        code_term = str(item.get("code_term") or "").strip()
        if not term or not code_term or len(term) > MAX_TERM_LENGTH:
            continue
        if not re.search(r"[\u4e00-\u9fff]", term):
            continue
        signature = (term, code_term)
        if signature in seen:
            continue
        seen.add(signature)
        try:
            line = int(item.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        file_name = str(item.get("file") or "").strip()
        evidence_ref = f"{file_name}:{line}" if file_name and line > 0 else file_name
        entries.append(
            GlossaryEntry(
                term=term,
                code_term=code_term,
                term_kind=str(item.get("term_kind") or "symbol"),
                source=str(item.get("source") or "comment"),
                evidence_ref=evidence_ref,
                confidence=float(item.get("confidence") or 0.8),
            )
        )
        if len(entries) >= limit:
            break
    return entries


async def replace_project_glossary_if_changed(
    pool,
    project: str,
    repository_fingerprint: str,
    index: Dict[str, Any],
    limit: int = MAX_KNOWLEDGE_BATCH,
) -> Dict[str, Any]:
    """在代码指纹变化时整体替换项目的注释派生术语。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `repository_fingerprint`：当前代码快照 SHA-256，类型为 `str`。
        - `index`：当前项目符号索引，类型为 `Dict[str, Any]`；只读取 `glossary`。
        - `limit`：单次最多写入条数，类型为 `int`。

    Returns:
        包含 `updated`、`entry_count`、`previous_count` 和 `skipped_reason` 的
        审计字典。同一指纹且条数一致时跳过写库；数据库异常返回
        `updated=False` 和 `skipped_reason=database_error`，不抛出给诊断主流程。
    """
    entries = extract_glossary(index, project, limit=limit)
    expected = len(entries)
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"project-glossary:{project}",
                )
                existing = await connection.fetchrow(
                    """
                    SELECT COUNT(*) AS count
                    FROM project_glossary
                    WHERE project = $1
                      AND source <> 'llm'
                      AND repository_fingerprint = $2
                    """,
                    project,
                    repository_fingerprint,
                )
                previous = int(existing["count"]) if existing is not None else 0
                if previous == expected and expected > 0:
                    return {
                        "updated": False,
                        "entry_count": expected,
                        "previous_count": previous,
                        "skipped_reason": "fingerprint_unchanged",
                    }
                # 删除旧指纹的注释派生词和模型沉淀词；同指纹的运行时词保留，
                # 避免每次索引重建都丢掉已经验证过的业务名。
                await connection.execute(
                    """
                    DELETE FROM project_glossary
                    WHERE project = $1
                      AND (source <> 'llm' OR repository_fingerprint <> $2)
                    """,
                    project,
                    repository_fingerprint,
                )
                if entries:
                    await connection.executemany(
                        """
                        INSERT INTO project_glossary
                            (project, term, code_term, term_kind, source,
                             evidence_ref, confidence, repository_fingerprint,
                             created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
                        ON CONFLICT (project, term, code_term) DO UPDATE SET
                            term_kind = EXCLUDED.term_kind,
                            source = EXCLUDED.source,
                            evidence_ref = EXCLUDED.evidence_ref,
                            confidence = GREATEST(
                                project_glossary.confidence,
                                EXCLUDED.confidence
                            ),
                            repository_fingerprint = EXCLUDED.repository_fingerprint,
                            updated_at = NOW()
                        """,
                        [
                            (
                                project,
                                item.term,
                                item.code_term,
                                item.term_kind,
                                item.source,
                                item.evidence_ref,
                                item.confidence,
                                repository_fingerprint,
                            )
                            for item in entries
                        ],
                    )
                return {
                    "updated": True,
                    "entry_count": expected,
                    "previous_count": previous,
                    "skipped_reason": "",
                }
    except Exception as exc:
        logger.warning(
            "project_glossary_replace_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return {
            "updated": False,
            "entry_count": expected,
            "previous_count": 0,
            "skipped_reason": "database_error",
        }


async def load_glossary_terms(
    pool,
    project: str,
    question: str,
    limit: int = 8,
    enabled: bool = True,
) -> Dict[str, Any]:
    """按当前问题查询知识库中的中文术语及其代码词。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `question`：用户原始问题，类型为 `str`；只用于中文子串匹配。
        - `limit`：最多返回的代码词数量，类型为 `int`。
        - `enabled`：知识库开关，类型为 `bool`；关闭时直接返回 `disabled`。

    Returns:
        包含 `status`、`terms`、`entries` 和 `reason` 的字典。状态取值：
        `disabled`（开关关闭）、`not_found`（无匹配）、`matched`（命中）、
        `degraded`（数据库异常）。`terms` 仍需调用方用符号索引做真实性校验。
    """
    if not enabled:
        return {"status": "disabled", "terms": [], "entries": [], "reason": "知识库已关闭"}
    if limit < 1:
        limit = 1
    try:
        rows = await pool.fetch(
            """
            SELECT term, code_term, term_kind, source, confidence
            FROM project_glossary
            WHERE project = $1
              AND position(term in $2) > 0
            ORDER BY confidence DESC, LENGTH(term) DESC, updated_at DESC
            LIMIT $3
            """,
            project,
            str(question or ""),
            limit,
        )
    except Exception as exc:
        logger.warning(
            "project_glossary_lookup_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return {"status": "degraded", "terms": [], "entries": [], "reason": "知识库查询失败"}
    entries = [dict(row) for row in rows]
    terms = list(dict.fromkeys(str(item["code_term"]) for item in entries))
    return {
        "status": "matched" if terms else "not_found",
        "terms": terms,
        "entries": entries,
        "reason": "" if terms else "知识库没有匹配术语",
    }


async def record_runtime_glossary(
    pool,
    project: str,
    repository_fingerprint: str,
    question: str,
    terms: Iterable[str],
) -> int:
    """把模型扩展词与问题中文锚点沉淀为低置信运行时术语。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `repository_fingerprint`：当前代码快照指纹，类型为 `str`。
        - `question`：用户原始问题，类型为 `str`；只提取中文锚点用于配对。
        - `terms`：已经通过符号索引校验的模型扩展词，类型为 `Iterable[str]`。

    Returns:
        实际写入或更新的条数。数据库异常返回 0，不抛出。
    """
    from app.code_tools import extract_generic_anchors

    code_terms = [str(item).strip() for item in terms if str(item).strip()][:8]
    if not code_terms:
        return 0
    try:
        anchors = extract_generic_anchors(question, max_anchors=8)
    except Exception:
        return 0
    chinese_terms = [
        re.sub(r"\s+", "", anchor)
        for anchor in anchors
        if re.search(r"[\u4e00-\u9fff]", anchor)
    ]
    chinese_terms = [term for term in chinese_terms if 2 <= len(term) <= MAX_TERM_LENGTH][:4]
    if not chinese_terms:
        return 0
    rows = [
        (
            project,
            term,
            code_term,
            "runtime",
            "llm",
            "",
            0.5,
            repository_fingerprint,
        )
        for code_term in code_terms
        for term in chinese_terms
    ][:MAX_KNOWLEDGE_BATCH]
    try:
        await pool.executemany(
            """
            INSERT INTO project_glossary
                (project, term, code_term, term_kind, source, evidence_ref,
                 confidence, repository_fingerprint, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
            ON CONFLICT (project, term, code_term) DO UPDATE SET
                repository_fingerprint = EXCLUDED.repository_fingerprint,
                updated_at = NOW()
            """,
            rows,
        )
    except Exception as exc:
        logger.warning(
            "project_glossary_runtime_write_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return 0
    return len(rows)


def _config_value_kind(value_text: str) -> str:
    """判断配置字面量应记录的值类型。

    参数:
        - `value_text`：`config_graph` 已过滤的安全值文本，类型为 `str`。

    Returns:
        `numeric`、`bool`、`constant` 或 `label`。无法识别的文本按 `label`
        处理，调用方仍需检查敏感键后再决定是否保存展示值。
    """
    text = str(value_text or "").strip()
    if _NUMERIC_PATTERN.fullmatch(text):
        return "numeric"
    if text.casefold() in {"true", "false"}:
        return "bool"
    if _CONSTANT_PATTERN.fullmatch(text):
        return "constant"
    return "label"


def _safe_display_value(key_name: str, value_text: str) -> Optional[str]:
    """返回可以落库的展示值，敏感键和地址类配置返回 `None`。

    参数:
        - `key_name`：配置键名，类型为 `str`；用于敏感字段判定。
        - `value_text`：待展示的值文本，类型为 `str`。

    Returns:
        普通业务标识符或数字/布尔/常量原样返回；敏感键、URL、路径、邮箱和含
        空格文本返回 `None`，保证知识库不扩大敏感信息暴露面。
    """
    text = str(value_text or "").strip()
    if not text or _SENSITIVE_KEY_PATTERN.search(key_name):
        return None
    if not _SAFE_VALUE_PATTERN.fullmatch(text):
        return None
    return text[:255]


async def record_config_structure(
    pool,
    project: str,
    repository_fingerprint: str,
    entries: Sequence[Any],
    env_values: Sequence[Any],
) -> int:
    """把诊断证据中的配置节点写入结构摘要表。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `repository_fingerprint`：当前代码快照指纹，类型为 `str`。
        - `entries`：`config_graph.ConfigEntry` 列表，类型为 `Sequence[Any]`；
          只读取 `file/path/value_text/branch/line`。
        - `env_values`：`config_graph.ConfigEnvValue` 列表，类型为
          `Sequence[Any]`；测试值和正式值会分别脱敏后再落库。

    Returns:
        实际写入或更新的节点数。数据库异常返回 0，不抛出。
    """
    rows: List[tuple] = []
    for entry in entries:
        try:
            path = ".".join(str(segment) for segment in entry.path)
            key_name = str(entry.key or "")
            kind = _config_value_kind(entry.value_text)
            display = _safe_display_value(key_name, entry.value_text)
            rows.append(
                (
                    project,
                    str(entry.file or ""),
                    path,
                    key_name,
                    kind,
                    display,
                    str(getattr(entry, "branch", "") or ""),
                    int(entry.line or 0),
                    repository_fingerprint,
                )
            )
        except Exception:
            continue
    for item in env_values:
        try:
            path = ".".join(str(segment) for segment in item.path)
            key_name = str(item.key or "")
            for kind, raw_value in (
                ("env_test", item.test_value),
                ("env_production", item.production_value),
            ):
                rows.append(
                    (
                        project,
                        str(item.file or ""),
                        path,
                        key_name,
                        kind,
                        _safe_display_value(key_name, raw_value),
                        str(getattr(item, "branch", "") or ""),
                        int(item.line or 0),
                        repository_fingerprint,
                    )
                )
        except Exception:
            continue
    rows = [row for row in rows if row[1] and row[3] and row[7] > 0][:MAX_KNOWLEDGE_BATCH]
    if not rows:
        return 0
    try:
        await pool.executemany(
            """
            INSERT INTO project_config_structure
                (project, file, array_path, key_name, value_kind, display_value,
                 branch, line, repository_fingerprint, observed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (project, file, array_path, key_name, branch) DO UPDATE SET
                value_kind = EXCLUDED.value_kind,
                display_value = EXCLUDED.display_value,
                line = EXCLUDED.line,
                repository_fingerprint = EXCLUDED.repository_fingerprint,
                observed_at = NOW()
            """,
            rows,
        )
    except Exception as exc:
        logger.warning(
            "project_config_structure_write_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return 0
    return len(rows)


async def load_config_structure(
    pool,
    project: str,
    file_name: str = "",
    key_name: str = "",
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """读取项目配置结构摘要，供知识库接口和运维排查使用。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `file_name`：可选配置文件过滤，类型为 `str`；空字符串表示不过滤。
        - `key_name`：可选配置键过滤，类型为 `str`；空字符串表示不过滤。
        - `limit`：最多返回条数，类型为 `int`，调用方应在 1 到 100 之间。

    Returns:
        结构摘要列表；数据库异常返回空列表。
    """
    try:
        rows = await pool.fetch(
            """
            SELECT file, array_path, key_name, value_kind, display_value, branch,
                   line, repository_fingerprint, observed_at
            FROM project_config_structure
            WHERE project = $1
              AND ($2 = '' OR file = $2)
              AND ($3 = '' OR key_name = $3)
            ORDER BY observed_at DESC, file, line
            LIMIT $4
            """,
            project,
            str(file_name or ""),
            str(key_name or ""),
            max(1, int(limit)),
        )
    except Exception as exc:
        logger.warning(
            "project_config_structure_read_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return []
    result: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        observed_at = item.get("observed_at")
        if hasattr(observed_at, "isoformat"):
            item["observed_at"] = observed_at.isoformat()
        result.append(item)
    return result


def _summarize_facts(facts: Sequence[Any]) -> List[Dict[str, str]]:
    """把结论事实压缩为可落库的类别和陈述摘要。

    参数:
        - `facts`：确定性结论事实列表，类型为 `Sequence[Any]`；只读取
          `category` 和 `statement`，不保存源码内容或证据正文。

    Returns:
        最多 20 条摘要；缺失类别或陈述的事实会被跳过。
    """
    summary: List[Dict[str, str]] = []
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        category = str(fact.get("category") or "").strip()
        statement = str(fact.get("statement") or "").strip()
        if not category and not statement:
            continue
        summary.append({"category": category, "statement": statement})
        if len(summary) >= 20:
            break
    return summary


async def save_candidate_conclusion(
    pool,
    project: str,
    repository_fingerprint: str,
    task_id: str,
    question: str,
    conclusion: str,
    facts: Sequence[Any],
    evidence_refs: Sequence[str],
) -> bool:
    """保存一条历史确定性结论作为检索候选。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `repository_fingerprint`：当前代码快照指纹，类型为 `str`。
        - `task_id`：生成结论的诊断任务 ID，类型为 `str`。
        - `question`：用户原始问题，类型为 `str`。
        - `conclusion`：确定性结论原文，类型为 `str`；空结论不写入。
        - `facts`：结论事实列表，类型为 `Sequence[Any]`。
        - `evidence_refs`：证据引用列表，类型为 `Sequence[str]`。

    Returns:
        写入成功返回 `True`；空结论或数据库异常返回 `False`。代码指纹变化时
        会把已有结论重置为 `candidate` 并延长有效期，避免旧结论继续被信任。
    """
    if not str(conclusion or "").strip():
        return False
    try:
        await pool.execute(
            """
            INSERT INTO project_verified_conclusions
                (project, question_hash, question, conclusion, confirmed_facts,
                 evidence_refs, status, source_task_id, repository_fingerprint,
                 expires_at, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, 'candidate', $7, $8,
                    NOW() + make_interval(days => $9), NOW(), NOW())
            ON CONFLICT (project, question_hash) DO UPDATE SET
                question = EXCLUDED.question,
                conclusion = EXCLUDED.conclusion,
                confirmed_facts = EXCLUDED.confirmed_facts,
                evidence_refs = EXCLUDED.evidence_refs,
                source_task_id = EXCLUDED.source_task_id,
                status = CASE
                    WHEN project_verified_conclusions.repository_fingerprint
                         <> EXCLUDED.repository_fingerprint
                    THEN 'candidate'
                    ELSE project_verified_conclusions.status
                END,
                repository_fingerprint = EXCLUDED.repository_fingerprint,
                verified_at = CASE
                    WHEN project_verified_conclusions.repository_fingerprint
                         <> EXCLUDED.repository_fingerprint
                    THEN NULL
                    ELSE project_verified_conclusions.verified_at
                END,
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW()
            """,
            project,
            question_hash(question),
            str(question or ""),
            str(conclusion),
            json.dumps(_summarize_facts(facts), ensure_ascii=False),
            json.dumps(list(dict.fromkeys(str(ref) for ref in evidence_refs)), ensure_ascii=False),
            str(task_id or ""),
            repository_fingerprint,
            CONCLUSION_TTL_DAYS,
        )
    except Exception as exc:
        logger.warning(
            "project_conclusion_write_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return False
    return True


async def load_conclusion_candidates(
    pool,
    project: str,
    question: str,
    repository_fingerprint: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """读取与当前问题和代码指纹一致的历史结论候选。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `question`：用户原始问题，类型为 `str`；按问题哈希精确匹配。
        - `repository_fingerprint`：当前代码快照指纹，类型为 `str`。
        - `limit`：最多返回条数，类型为 `int`。

    Returns:
        候选列表，字段包含 `question`、`conclusion`、`evidence_refs`、`status`
        和 `expires_at`。只返回未过期且指纹一致的数据；调用方只能把它作为
        检索提示，不能拼进新结论。
    """
    try:
        rows = await pool.fetch(
            """
            SELECT question, conclusion, evidence_refs, status, expires_at
            FROM project_verified_conclusions
            WHERE project = $1
              AND question_hash = $2
              AND repository_fingerprint = $3
              AND status IN ('candidate', 'verified')
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY CASE status WHEN 'verified' THEN 0 ELSE 1 END, updated_at DESC
            LIMIT $4
            """,
            project,
            question_hash(question),
            repository_fingerprint,
            max(1, int(limit)),
        )
    except Exception as exc:
        logger.warning(
            "project_conclusion_read_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return []
    result: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        expires_at = item.get("expires_at")
        if hasattr(expires_at, "isoformat"):
            item["expires_at"] = expires_at.isoformat()
        evidence_refs = item.get("evidence_refs")
        if isinstance(evidence_refs, str):
            try:
                item["evidence_refs"] = json.loads(evidence_refs)
            except ValueError:
                item["evidence_refs"] = []
        result.append(item)
    return result


async def apply_feedback_to_conclusion(
    pool,
    project: str,
    question: str,
    rating: str,
) -> str:
    """根据用户反馈更新历史结论状态。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。
        - `question`：用户原始问题，类型为 `str`；用于定位结论哈希。
        - `rating`：反馈评级，类型为 `str`；`correct` 升级为验证通过，
          `inaccurate` 标记为拒绝，其余评级不做状态变更。

    Returns:
        更新后的状态；未更新或数据库异常返回空字符串。反馈接口不得因该函数
        失败而报错。
    """
    normalized = str(rating or "").strip().casefold()
    if normalized not in {"correct", "inaccurate"}:
        return ""
    try:
        if normalized == "correct":
            row = await pool.fetchrow(
                """
                UPDATE project_verified_conclusions
                SET status = 'verified',
                    verified_at = NOW(),
                    expires_at = NOW() + make_interval(days => $3),
                    updated_at = NOW()
                WHERE project = $1 AND question_hash = $2
                RETURNING status
                """,
                project,
                question_hash(question),
                CONCLUSION_TTL_DAYS,
            )
        else:
            row = await pool.fetchrow(
                """
                UPDATE project_verified_conclusions
                SET status = 'rejected',
                    verified_at = NULL,
                    updated_at = NOW()
                WHERE project = $1 AND question_hash = $2
                RETURNING status
                """,
                project,
                question_hash(question),
            )
        return str(row["status"]) if row is not None else ""
    except Exception as exc:
        logger.warning(
            "project_conclusion_feedback_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
        )
        return ""


async def rebuild_glossary_from_code_symbols(pool, project: str) -> Dict[str, Any]:
    """只读数据库重新校验并刷新项目术语表。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool` 或测试替身。
        - `project`：项目标识，类型为 `str`。

    Returns:
        包含 `status`、`fingerprint`、`entry_count` 和 `removed_count` 的字典。
        重建不扫描仓库，只把仍能命中 `code_symbols` 或文件路径的术语更新到当前
        指纹，并删除已不存在的代码词；新注释派生词会在下一次诊断索引时自动补齐。
    """
    row = await pool.fetchrow(
        "SELECT repository_fingerprint FROM project_symbol_indexes WHERE project = $1",
        project,
    )
    if row is None:
        return {"status": "not_indexed", "fingerprint": "", "entry_count": 0, "removed_count": 0}
    fingerprint = str(row["repository_fingerprint"])
    symbol_rows = await pool.fetch(
        """
        SELECT symbol_name, file_path
        FROM code_symbols
        WHERE project = $1
        """,
        project,
    )
    known = {
        str(item["symbol_name"]).split("::")[-1]
        for item in symbol_rows
        if str(item["symbol_name"] or "").strip()
    }
    files = {
        str(item["file_path"]).replace("\\", "/")
        for item in symbol_rows
        if str(item["file_path"] or "").strip()
    }
    glossary_rows = await pool.fetch(
        """
        SELECT term, code_term, repository_fingerprint
        FROM project_glossary
        WHERE project = $1
        """,
        project,
    )
    removed: List[str] = []
    kept: List[str] = []
    for item in glossary_rows:
        code_term = str(item["code_term"] or "")
        if code_term in known or any(code_term in path for path in files):
            kept.append(code_term)
        else:
            removed.append(code_term)
    if removed:
        # 只删除已失效代码词，保留仍然存在的运行时术语。
        await pool.execute(
            """
            DELETE FROM project_glossary
            WHERE project = $1
              AND code_term = ANY($2::varchar[])
            """,
            project,
            sorted({term for term in removed if term}),
        )
    await pool.execute(
        """
        UPDATE project_glossary
        SET repository_fingerprint = $2, updated_at = NOW()
        WHERE project = $1 AND repository_fingerprint <> $2
        """,
        project,
        fingerprint,
    )
    return {
        "status": "revalidated",
        "fingerprint": fingerprint,
        "entry_count": len(kept),
        "removed_count": len(removed),
    }
