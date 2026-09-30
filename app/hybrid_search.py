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


def _tokens(query: str) -> List[str]:
    """提取中英文检索词并去重，供符号结果匹配和排序使用。"""
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
    """从已完成诊断任务中召回包含关键词的问题，并仅返回安全摘要。"""
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
) -> Dict[str, Any]:
    """合并代码关键词、PHP 符号和历史问题召回结果并按分数排序。"""
    query = query.strip()
    if not query or len(query) > 200:
        raise CodeToolError("检索关键词不能为空且不能超过 200 个字符")
    if limit < 1 or limit > 100:
        raise CodeToolError("检索结果数量必须在 1 到 100 之间")
    if history_offset < 0 or history_offset > 10_000:
        raise CodeToolError("历史检索偏移量必须在 0 到 10000 之间")
    if history_days is not None and (history_days < 1 or history_days > 3650):
        raise CodeToolError("历史检索时间范围必须在 1 到 3650 天之间")
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
    unique: Dict[str, Dict[str, Any]] = {}
    for item in results:
        unique.setdefault(f"{item['source_type']}:{item['source_id']}", item)
    ranked = sorted(unique.values(), key=lambda item: (-item["score"], item["source_id"]))[:limit]
    return {"project": project, "query": query, "results": ranked, "total": len(ranked)}
