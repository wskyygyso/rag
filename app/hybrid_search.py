"""代码、符号和历史诊断结果的轻量级混合检索。"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from app.code_tools import CodeToolError, search_code
from app.document_chunks import search_document_chunks
from app.php_index import index_repository
from app.validation_tools import redact_sensitive

SOURCE_WEIGHTS = {"code": 1.0, "symbol": 0.9, "chunk": 0.85, "history": 0.6}


def _tokens(query: str) -> List[str]:
    """提取中英文检索词并去重，供符号结果匹配和排序使用。

    参数:
        - `query`：检索或查询文本，类型为 `str`。
    """
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_:]*|[\u4e00-\u9fff]{2,}", query.lower())
    return list(dict.fromkeys(words))


async def _search_history(
    pool: asyncpg.Pool,
    project: str,
    query: str,
    limit: int,
    offset: int,
    history_days: Optional[int],
) -> List[Dict[str, Any]]:
    """从已完成诊断任务中召回包含关键词的问题，并仅返回安全摘要。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `query`：检索或查询文本，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
        - `offset`：分页起始偏移，类型为 `int`。
        - `history_days`：历史诊断结果最近天数范围，类型为 `Optional[int]`。
    """
    since = (
        datetime.now(timezone.utc) - timedelta(days=history_days)
        if history_days is not None
        else None
    )
    rows = await pool.fetch(
        """
        SELECT id, question, status, updated_at
        FROM diagnostic_tasks
        WHERE project = $1 AND status = 'COMPLETED' AND question ILIKE $2
          AND ($3::timestamptz IS NULL OR updated_at >= $3)
        ORDER BY updated_at DESC
        LIMIT $4 OFFSET $5
        """,
        project,
        f"%{query}%",
        since,
        limit,
        offset,
    )
    return [
        {
            "source_type": "history",
            "source_id": row["id"],
            "score": 0.6,
            "project": project,
            "question": redact_sensitive(str(row["question"])),
            "status": row["status"],
            "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
        }
        for row in rows
    ]


async def search_hybrid(
    root: str,
    project: str,
    query: str,
    pool: asyncpg.Pool,
    limit: int = 20,
    history_offset: int = 0,
    history_days: Optional[int] = None,
    source_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """合并多来源召回结果，按来源权重和原始相关性分数统一排序。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `query`：检索或查询文本，类型为 `str`。
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `limit`：单次结果数量上限，类型为 `int`。
        - `history_offset`：历史诊断结果分页偏移，类型为 `int`。
        - `history_days`：历史诊断结果最近天数范围，类型为 `Optional[int]`。
        - `source_types`：混合检索来源筛选类型，类型为 `Optional[List[str]]`。
    """
    query = query.strip()
    if not query or len(query) > 200:
        raise CodeToolError("检索关键词不能为空且不能超过 200 个字符")
    if limit < 1 or limit > 100:
        raise CodeToolError("检索结果数量必须在 1 到 100 之间")
    if history_offset < 0 or history_offset > 10_000:
        raise CodeToolError("历史检索偏移量必须在 0 到 10000 之间")
    if history_days is not None and (history_days < 1 or history_days > 3650):
        raise CodeToolError("历史检索时间范围必须在 1 到 3650 天之间")
    if source_types is not None:
        source_types = list(dict.fromkeys(source_types))
        if not source_types or any(item not in SOURCE_WEIGHTS for item in source_types):
            raise CodeToolError("来源类型只能是 code、symbol、chunk 或 history")
    tokens = _tokens(query) or [query.lower()]
    results: List[Dict[str, Any]] = []
    lexical = await asyncio.to_thread(search_code, root, project, query, max_results=limit)
    for item in lexical["matches"]:
        results.append({
            "source_type": "code",
            "source_id": f"{item['file']}:{item['line']}",
            "score": 1.0,
            "project": project,
            **item,
        })
    index = await asyncio.to_thread(index_repository, root, project)
    for symbol in index["symbols"]:
        name = symbol["symbol_name"].lower()
        if any(token in name for token in tokens):
            results.append({
                "source_type": "symbol",
                "source_id": f"{symbol['file']}:{symbol['line']}:{symbol['symbol_name']}",
                "score": 0.9,
                "project": project,
                **symbol,
            })
    results.extend(await _search_history(pool, project, query, limit, history_offset, history_days))
    for item in await search_document_chunks(pool, project, query, limit):
        item["score"] = min(0.85, max(0.1, item["score"]))
        results.append(item)
    if source_types is not None:
        results = [item for item in results if item["source_type"] in source_types]
    unique: Dict[str, Dict[str, Any]] = {}
    for item in results:
        item["raw_score"] = item["score"]
        item["score"] = round(item["score"] * SOURCE_WEIGHTS[item["source_type"]], 6)
        unique.setdefault(f"{item['source_type']}:{item['source_id']}", item)
    ranked = sorted(unique.values(), key=lambda item: (-item["score"], item["source_id"]))[:limit]
    return {"project": project, "query": query, "results": ranked, "total": len(ranked)}
