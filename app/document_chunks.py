"""代码、配置和历史文档统一分块模型及 PostgreSQL 持久化。"""

import hashlib
import json
from typing import Any, Dict, List, Optional

import asyncpg

from app.code_tools import CodeRepository, CodeToolError
from app.embeddings import EMBEDDING_DIMENSION, EMBEDDING_MODEL, embed_text, cosine_similarity

TEXT_EXTENSIONS = {".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json", ".md", ".txt"}
IGNORED_DIRECTORIES = {".git", "vendor", "node_modules", "runtime", "storage", "cache"}


def chunk_text(
    project: str,
    source_type: str,
    source_id: str,
    content: str,
    *,
    file_path: Optional[str] = None,
    max_chars: int = 4000,
    overlap_lines: int = 3,
) -> List[Dict[str, Any]]:
    """按行切分文本，保留来源、行号、哈希和少量上下文重叠。"""
    if not project or not source_type or not source_id:
        raise ValueError("分块来源标识不能为空")
    if max_chars < 100 or max_chars > 50_000:
        raise ValueError("分块大小必须在 100 到 50000 个字符之间")
    if overlap_lines < 0 or overlap_lines > 20:
        raise ValueError("分块重叠行数必须在 0 到 20 行之间")
    lines = content.splitlines()
    chunks: List[Dict[str, Any]] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines):
            next_size = size + len(lines[end]) + (1 if end > start else 0)
            if end > start and next_size > max_chars:
                break
            size = next_size
            end += 1
        text = "\n".join(lines[start:end])
        chunk_id = f"{source_id}:{start + 1}-{end}"
        chunks.append(
            {
                "project": project,
                "source_type": source_type,
                "source_id": chunk_id,
                "file_path": file_path,
                "line_start": start + 1,
                "line_end": end,
                "content": text,
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "embedding": embed_text(text),
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dimension": EMBEDDING_DIMENSION,
                "metadata": {"parent_source_id": source_id},
            }
        )
        if end >= len(lines):
            break
        start = max(start + 1, end - overlap_lines)
    return chunks


def build_repository_chunks(root: str, project: str, max_chars: int = 4000) -> List[Dict[str, Any]]:
    """扫描项目文本文件并生成可持久化的代码、配置和文档分块。"""
    try:
        repository = CodeRepository(root, project).path
    except CodeToolError as exc:
        raise ValueError(str(exc)) from exc
    chunks: List[Dict[str, Any]] = []
    for path in sorted(repository.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = str(path.relative_to(repository))
        source_type = "code" if path.suffix.lower() in {".php", ".inc", ".phtml"} else "document"
        chunks.extend(
            chunk_text(
                project,
                source_type,
                relative,
                content,
                file_path=relative,
                max_chars=max_chars,
            )
        )
    return chunks


async def index_repository_documents(
    root: str, project: str, pool: asyncpg.Pool, max_chars: int = 4000
) -> int:
    """扫描项目并事务性替换统一分块索引，返回写入分块数量。"""
    chunks = build_repository_chunks(root, project, max_chars=max_chars)
    return await sync_document_chunks(pool, project, chunks)


async def search_document_chunks(
    pool: asyncpg.Pool, project: str, query: str, limit: int = 20
) -> List[Dict[str, Any]]:
    """使用 PostgreSQL 全文索引召回项目分块，并返回来源和相关性分数。"""
    rows = await pool.fetch(
        """
        SELECT source_type, source_id, file_path, line_start, line_end, content, embedding,
               embedding_model, embedding_dimension,
               ts_rank(search_vector, plainto_tsquery('simple', $2)) AS lexical_score
        FROM document_chunks
        WHERE project = $1
        ORDER BY updated_at DESC, source_id
        LIMIT 1000
        """,
        project,
        query,
    )
    query_vector = embed_text(query)
    scored = []
    for row in rows:
        lexical_score = min(1.0, max(0.0, float(row["lexical_score"] or 0)))
        embedding = row.get("embedding") if hasattr(row, "get") else None
        embedding_model = row.get("embedding_model") if hasattr(row, "get") else None
        embedding_dimension = row.get("embedding_dimension") if hasattr(row, "get") else None
        vector_compatible = (
            embedding_model == EMBEDDING_MODEL
            and embedding_dimension == EMBEDDING_DIMENSION
            and bool(embedding)
        )
        vector_score = max(0.0, cosine_similarity(query_vector, embedding)) if vector_compatible else 0.0
        score = round(0.6 * lexical_score + 0.4 * vector_score, 6)
        if score <= 0:
            continue
        scored.append((score, row, lexical_score, vector_score, vector_compatible))
    scored.sort(key=lambda item: (-item[0], item[1]["source_id"]))
    return [
        {
            "source_type": "chunk",
            "source_id": row["source_id"],
            "project": project,
            "file": row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "content": row["content"],
            "score": score,
            "lexical_score": lexical_score,
            "vector_score": vector_score,
            "embedding_model": row.get("embedding_model") if hasattr(row, "get") else None,
            "embedding_dimension": row.get("embedding_dimension") if hasattr(row, "get") else None,
            "vector_compatible": vector_compatible,
        }
        for score, row, lexical_score, vector_score, vector_compatible in scored[:limit]
    ]


async def replace_document_chunks(
    pool: asyncpg.Pool, project: str, chunks: List[Dict[str, Any]]
) -> int:
    """兼容旧调用的项目分块同步入口。"""
    return await sync_document_chunks(pool, project, chunks)


async def sync_document_chunks(
    pool: asyncpg.Pool, project: str, chunks: List[Dict[str, Any]]
) -> int:
    """增量同步项目分块，仅更新变化内容并清理已删除来源。"""
    source_types = sorted({chunk["source_type"] for chunk in chunks})
    source_ids = sorted({chunk["source_id"] for chunk in chunks})
    async with pool.acquire() as connection:
        async with connection.transaction():
            for chunk in chunks:
                await connection.execute(
                    """
                    INSERT INTO document_chunks
                        (project, source_type, source_id, file_path, line_start, line_end,
                         content, content_hash, embedding, embedding_model, embedding_dimension, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12::jsonb)
                    ON CONFLICT (project, source_type, source_id)
                    DO UPDATE SET content = EXCLUDED.content,
                                  content_hash = EXCLUDED.content_hash,
                                  embedding = EXCLUDED.embedding,
                                  embedding_model = EXCLUDED.embedding_model,
                                  embedding_dimension = EXCLUDED.embedding_dimension,
                                  metadata = EXCLUDED.metadata,
                                  updated_at = NOW()
                    """,
                    project,
                    chunk["source_type"],
                    chunk["source_id"],
                    chunk.get("file_path"),
                    chunk.get("line_start"),
                    chunk.get("line_end"),
                    chunk["content"],
                    chunk["content_hash"],
                    json.dumps(chunk.get("embedding", []), ensure_ascii=False),
                    chunk.get("embedding_model", EMBEDDING_MODEL),
                    chunk.get("embedding_dimension", EMBEDDING_DIMENSION),
                    json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                )
            if source_types:
                await connection.execute(
                    """
                    DELETE FROM document_chunks
                    WHERE project = $1
                      AND source_type = ANY($2::text[])
                      AND NOT (source_id = ANY($3::text[]))
                    """,
                    project,
                    source_types,
                    source_ids,
                )
            else:
                await connection.execute("DELETE FROM document_chunks WHERE project = $1", project)
    return len(chunks)
