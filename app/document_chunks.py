"""代码、配置和历史文档统一分块模型及 PostgreSQL 持久化。"""

import asyncio
import hashlib
import json
import math
import re
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional

import asyncpg

from app.code_tools import CodeRepository, CodeToolError
from app.embeddings import (
    EmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)
from app.repository_files import iter_repository_files

TEXT_EXTENSIONS = {".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json", ".md", ".txt"}

# 这些词属于 PHP 语法和通用结构，不能帮助区分业务。过滤它们可以避免每个
# 代码分块都出现相似的 class/function/return 噪声，同时不绑定任何业务领域。
_EMBEDDING_CODE_STOPWORDS = {
    "abstract",
    "array",
    "bool",
    "break",
    "callable",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "declare",
    "default",
    "echo",
    "else",
    "elseif",
    "empty",
    "extends",
    "false",
    "final",
    "finally",
    "float",
    "for",
    "foreach",
    "function",
    "global",
    "if",
    "implements",
    "include",
    "instanceof",
    "int",
    "interface",
    "isset",
    "iterable",
    "mixed",
    "namespace",
    "new",
    "null",
    "object",
    "parent",
    "php",
    "private",
    "protected",
    "public",
    "require",
    "return",
    "self",
    "static",
    "string",
    "switch",
    "throw",
    "trait",
    "true",
    "try",
    "unset",
    "use",
    "void",
    "while",
    "yield",
}


def _decode_embedding_vector(value: Any) -> Optional[List[float]]:
    """把数据库或测试替身返回的 JSONB 向量转换为有限浮点列表。

    参数:
        - `value`：`document_chunks.embedding` 的原始读取值。生产环境中
          `asyncpg` 可能把未注册 JSONB 编解码器的字段返回为 JSON 字符串，
          测试替身则通常直接返回 Python 列表。

    Returns:
        可用于余弦相似度计算的浮点列表；空值、损坏 JSON、非数组、布尔值、
        非数值元素或 NaN/Infinity 均返回 `None`。失败时静默降级为无向量，
        避免单个损坏索引行中断整个项目检索。
    """
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(parsed, (list, tuple)) or not parsed:
        return None

    vector: List[float] = []
    for item in parsed:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numeric = float(item)
        if not math.isfinite(numeric):
            return None
        vector.append(numeric)
    return vector


def _append_long_line_chunks(
    project: str,
    source_type: str,
    source_id: str,
    file_path: Optional[str],
    line_number: int,
    line: str,
    max_chars: int,
    chunks: List[Dict[str, Any]],
) -> None:
    """将超长单行拆成可写入 PostgreSQL 全文索引的字符分块。

    参数:
        - `project`：项目目录名，决定分块的检索隔离边界。
        - `source_type`：来源类型，取值为 `code` 或 `document`。
        - `source_id`：文件相对路径，作为分块稳定来源标识。
        - `file_path`：文件在项目内的相对路径，用于报告回链。
        - `line_number`：超长内容所在的一基物理行号。
        - `line`：需要拆分的原始单行内容，不会修改目标文件。
        - `max_chars`：每个字符分块允许的最大字符数。
        - `chunks`：当前文件的累计分块列表，函数直接追加拆分结果。

    返回:
        无返回值；长行会生成带 `part-N` 后缀的稳定来源 ID，避免同一行多个
        分块在数据库唯一约束中互相覆盖。相邻分块保留有限字符重叠，便于命中
        跨越分块边界的符号或长字符串。
    """
    overlap_chars = min(200, max_chars // 10)
    offset = 0
    part_number = 1
    while offset < len(line):
        end = min(len(line), offset + max_chars)
        text = line[offset:end]
        chunks.append(
            {
                "project": project,
                "source_type": source_type,
                "source_id": f"{source_id}:{line_number}-{line_number}:part-{part_number}",
                "file_path": file_path,
                "line_start": line_number,
                "line_end": line_number,
                "content": text,
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "metadata": {
                    "parent_source_id": source_id,
                    "character_start": offset + 1,
                    "character_end": end,
                },
            }
        )
        if end >= len(line):
            break
        offset = max(offset + 1, end - overlap_chars)
        part_number += 1


def chunk_text(
    project: str,
    source_type: str,
    source_id: str,
    content: str,
    *,
    file_path: Optional[str] = None,
    max_chars: int = 4000,
    overlap_lines: int = 3,
    embedding_provider: Optional[EmbeddingProvider] = None,
    embed: bool = True,
) -> List[Dict[str, Any]]:
    """按行切分文本，保留来源、行号、哈希和少量上下文重叠。

    参数:
        - `project`：项目标识，类型为 `str`。
        - `source_type`：分块来源类型，类型为 `str`。
        - `source_id`：分块来源的稳定标识，类型为 `str`。
        - `content`：待分块、解析或返回的文本内容，类型为 `str`。
        - `file_path`：源代码或文档文件路径，类型为 `Optional[str]`。
        - `max_chars`：单块或单字段最大字符数，类型为 `int`。
        - `overlap_lines`：相邻文本分块重叠行数，类型为 `int`。
        - `embedding_provider`：生成真实或离线向量的提供方；为空时使用离线哈希
          provider，类型为 `Optional[EmbeddingProvider]`。
        - `embed`：是否立即生成向量；仓库级索引会先汇总全部分块再批量生成，
          因此内部扫描阶段传 `False`，类型为 `bool`。
    """
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
        if len(lines[start]) > max_chars:
            _append_long_line_chunks(
                project,
                source_type,
                source_id,
                file_path,
                start + 1,
                lines[start],
                max_chars,
                chunks,
            )
            start += 1
            continue
        end = start
        size = 0
        while end < len(lines):
            if len(lines[end]) > max_chars:
                break
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
                "metadata": {"parent_source_id": source_id},
            }
        )
        if end >= len(lines):
            break
        start = max(start + 1, end - overlap_lines)
    if embed:
        _attach_embeddings(chunks, embedding_provider or get_embedding_provider())
    return chunks


def _expand_code_identifier(identifier: str) -> List[str]:
    """把蛇形、全大写缩写和驼峰标识符拆成可跨语言理解的英文词。

    参数:
        - `identifier`：源码中的单个标识符，例如 `maxUsersPerDevice` 或
          `minimum_withdrawal_diamonds`。

    Returns:
        小写英文词列表。拆分只依据命名形式，不包含任何业务词典或中英文映射。
    """
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", str(identifier or ""))
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [
        part.strip("_").casefold()
        for part in re.split(r"[^A-Za-z0-9]+", value)
        if part.strip("_") and len(part.strip("_")) >= 2
    ]


def embedding_text_for_chunk(chunk: Dict[str, Any]) -> str:
    """把代码分块转换为适合真实 embedding 的自然语言描述文本。

    参数:
        - `chunk`：已完成切分的代码或文档分块，类型为 `Dict[str, Any]`；代码
          分块读取文件路径、标识符和中文注释，文档分块保留原文。

    Returns:
        embedding 输入文本。代码标识符会按驼峰和下划线拆词，减少 PHP 标点和
        通用语法对中文问题跨语言相似度的干扰；文档和配置仍使用原始内容。
    """
    source_type = str(chunk.get("source_type") or "")
    content = str(chunk.get("content") or "")
    if source_type != "code":
        return content

    words: List[str] = []
    seen = set()
    file_name = str(chunk.get("file_path") or "")
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", f"{file_name} {content}")
    for identifier in identifiers:
        for word in _expand_code_identifier(identifier):
            if word in _EMBEDDING_CODE_STOPWORDS or word in seen:
                continue
            seen.add(word)
            words.append(word)
            if len(words) >= 320:
                break
        if len(words) >= 320:
            break
    chinese_labels = list(dict.fromkeys(re.findall(r"[\u4e00-\u9fff]{2,}", content)))[:80]
    parts = ["Source code business terms"]
    if words:
        parts.append("Identifiers: " + " ".join(words))
    if chinese_labels:
        parts.append("Chinese labels: " + " ".join(chinese_labels))
    return ". ".join(parts)


def _attach_embeddings(
    chunks: List[Dict[str, Any]],
    embedding_provider: EmbeddingProvider,
) -> None:
    """为一批分块生成向量并写入模型兼容元数据。

    参数:
        - `chunks`：已完成文本切分的分块记录，函数原地修改，类型为
          `List[Dict[str, Any]]`。
        - `embedding_provider`：真实或离线向量提供方，类型为
          `EmbeddingProvider`；批量接口用于减少 HTTP 往返次数。

    Raises:
        EmbeddingProviderError：真实向量服务失败时直接抛出，索引不得把空向量
          当成功结果写入。
    """
    if not chunks:
        return
    batch_size = max(1, int(getattr(embedding_provider, "batch_size", 8) or 8))
    concurrency = max(1, int(getattr(embedding_provider, "concurrency", 1) or 1))
    batches = [chunks[offset:offset + batch_size] for offset in range(0, len(chunks), batch_size)]

    def embed_batch(index: int) -> tuple[int, List[Dict[str, Any]], List[List[float]]]:
        """生成一个批次向量并返回原始位置，供并发结果保持分块顺序。

        参数:
            - `index`：批次在仓库分块列表中的从零开始位置。

        Returns:
            批次索引、批次分块和与分块一一对应的向量。任一请求失败都会由未来
            对象把异常传回主线程，索引事务不会写入部分结果。
        """
        batch = batches[index]
        vectors = embedding_provider.embed_many(embedding_text_for_chunk(chunk) for chunk in batch)
        if len(vectors) != len(batch):
            raise ValueError("embedding 返回数量与分块数量不一致")
        return index, batch, vectors

    completed: dict[int, tuple[List[Dict[str, Any]], List[List[float]]]] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures: dict[Future, int] = {
            executor.submit(embed_batch, index): index for index in range(len(batches))
        }
        try:
            for future in as_completed(futures):
                index, batch, vectors = future.result()
                completed[index] = (batch, vectors)
        except Exception:
            for future in futures:
                future.cancel()
            raise

    for index in range(len(batches)):
        batch, vectors = completed[index]
        for chunk, vector in zip(batch, vectors):
            chunk["embedding"] = vector
            chunk["embedding_model"] = embedding_provider.model_name
            chunk["embedding_dimension"] = embedding_provider.dimension


def build_repository_chunks(
    root: str,
    project: str,
    max_chars: int = 4000,
    *,
    embedding_provider: Optional[EmbeddingProvider] = None,
    embed: bool = True,
) -> List[Dict[str, Any]]:
    """扫描项目文本文件并生成可持久化的代码、配置和文档分块。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `max_chars`：单块或单字段最大字符数，类型为 `int`。
        - `embedding_provider`：索引使用的向量提供方；为空时使用离线哈希 provider。
        - `embed`：是否生成向量；dry-run 只统计分块时可关闭，避免调用外部服务。
    """
    try:
        repository = CodeRepository(root, project).path
    except CodeToolError as exc:
        raise ValueError(str(exc)) from exc
    chunks: List[Dict[str, Any]] = []
    for path in iter_repository_files(repository, TEXT_EXTENSIONS):
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
                embed=False,
            )
        )
    if embed:
        _attach_embeddings(chunks, embedding_provider or get_embedding_provider())
    return chunks


async def index_repository_documents(
    root: str,
    project: str,
    pool: asyncpg.Pool,
    max_chars: int = 4000,
    *,
    embedding_provider: Optional[EmbeddingProvider] = None,
) -> int:
    """扫描项目并事务性替换统一分块索引，返回写入分块数量。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `max_chars`：单块或单字段最大字符数，类型为 `int`。
        - `embedding_provider`：真实向量提供方；为空时使用离线哈希 provider。
    """
    chunks = await asyncio.to_thread(
        build_repository_chunks,
        root,
        project,
        max_chars,
        embedding_provider=embedding_provider or get_embedding_provider(),
        embed=True,
    )
    return await bulk_replace_document_chunks(pool, project, chunks)


async def search_document_chunks(
    pool: asyncpg.Pool,
    project: str,
    query: str,
    limit: int = 20,
    *,
    embedding_provider: Optional[EmbeddingProvider] = None,
    scan_limit: int = 20000,
    compatible_only: bool = False,
    source_types: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """使用 PostgreSQL 全文索引召回项目分块，并返回来源和相关性分数。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `query`：检索或查询文本，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
        - `embedding_provider`：与索引一致的向量提供方；为空时使用离线哈希 provider。
        - `scan_limit`：单次在 Python 中计算余弦相似度的最大分块数，用于限制
          大型项目内存占用和查询延迟，类型为 `int`。
        - `compatible_only`：是否只读取当前 provider 模型和维度兼容的分块。真实
          语义检索必须开启，避免旧模型向量或尚未完成重建的分块占满扫描窗口；
          通用全文检索保持关闭，以便继续使用词法结果。
        - `source_types`：需要限制的原始来源类型，例如真实语义源码通道传入
          `("code",)`；为空时不限制代码或文档。
    """
    if limit < 1 or limit > 200:
        raise ValueError("文档检索数量必须在 1 到 200 之间")
    if scan_limit < limit or scan_limit > 100_000:
        raise ValueError("向量扫描上限必须在结果数量到 100000 之间")
    provider = embedding_provider or get_embedding_provider()
    query_vector = await asyncio.to_thread(provider.embed, query)
    allowed_source_types = tuple(
        sorted({str(value).strip() for value in source_types or () if str(value).strip()})
    )
    if compatible_only or allowed_source_types:
        parameters: List[Any] = [project, query, scan_limit]
        filters = []
        if allowed_source_types:
            parameters.append(list(allowed_source_types))
            filters.append(f"source_type = ANY(${len(parameters)}::text[])")
        if compatible_only:
            parameters.append(provider.model_name)
            filters.append(f"embedding_model = ${len(parameters)}")
            parameters.append(provider.dimension)
            filters.append(f"embedding_dimension = ${len(parameters)}")
        filter_sql = "\n              AND ".join(filters)
        rows = await pool.fetch(
            f"""
            SELECT source_type, source_id, file_path, line_start, line_end, content, embedding,
                   embedding_model, embedding_dimension,
                   ts_rank(search_vector, plainto_tsquery('simple', $2)) AS lexical_score
            FROM document_chunks
            WHERE project = $1
              AND {filter_sql}
              AND embedding IS NOT NULL
            ORDER BY updated_at DESC, source_id
            LIMIT $3
            """,
            *parameters,
        )
    else:
        rows = await pool.fetch(
            """
        SELECT source_type, source_id, file_path, line_start, line_end, content, embedding,
               embedding_model, embedding_dimension,
               ts_rank(search_vector, plainto_tsquery('simple', $2)) AS lexical_score
        FROM document_chunks
        WHERE project = $1
        ORDER BY updated_at DESC, source_id
        LIMIT $3
        """,
            project,
            query,
            scan_limit,
        )
    scored = []
    for row in rows:
        lexical_score = min(1.0, max(0.0, float(row["lexical_score"] or 0)))
        # asyncpg 默认可能把 JSONB 读成字符串；在评分前统一解码，否则真实模型
        # 向量会被误判为维度不兼容，导致语义通道明明有索引却返回零命中。
        embedding = _decode_embedding_vector(
            row.get("embedding") if hasattr(row, "get") else None
        )
        embedding_model = row.get("embedding_model") if hasattr(row, "get") else None
        embedding_dimension = row.get("embedding_dimension") if hasattr(row, "get") else None
        vector_compatible = (
            embedding_model == provider.model_name
            and embedding_dimension == provider.dimension
            and bool(embedding)
        )
        vector_score = max(0.0, cosine_similarity(query_vector, embedding)) if vector_compatible else 0.0
        score = round(
            0.35 * lexical_score + 0.65 * vector_score
            if vector_compatible
            else 0.6 * lexical_score,
            6,
        )
        if score <= 0:
            continue
        scored.append((score, row, lexical_score, vector_score, vector_compatible))
    scored.sort(key=lambda item: (-item[0], item[1]["source_id"]))
    return [
        {
            "source_type": "chunk",
            # 保留分块在索引中的原始来源类型。语义通道必须确认它来自 code，
            # 不能把历史文档或配置分块的相似度当成 PHP 源码证据。
            "document_source_type": row.get("source_type") if hasattr(row, "get") else None,
            "source_id": row["source_id"],
            "project": project,
            "file": row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "content": row["content"],
            "score": score,
            "lexical_score": lexical_score,
            "vector_score": vector_score,
            "semantic_vector": vector_compatible,
            "embedding_model": row.get("embedding_model") if hasattr(row, "get") else None,
            "embedding_dimension": row.get("embedding_dimension") if hasattr(row, "get") else None,
            "vector_compatible": vector_compatible,
        }
        for score, row, lexical_score, vector_score, vector_compatible in scored[:limit]
    ]


async def replace_document_chunks(
    pool: asyncpg.Pool, project: str, chunks: List[Dict[str, Any]]
) -> int:
    """兼容旧调用的项目分块同步入口。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `chunks`：待写入或待统计的文档分块集合，类型为 `List[Dict[str, Any]]`。
    """
    return await sync_document_chunks(pool, project, chunks)


async def bulk_replace_document_chunks(
    pool: asyncpg.Pool,
    project: str,
    chunks: List[Dict[str, Any]],
) -> int:
    """在单个事务内用 PostgreSQL COPY 全量替换项目分块。

    参数:
        - `pool`：RAgent 元数据库连接池，类型为 `asyncpg.Pool`。
        - `project`：当前重建的项目标识，类型为 `str`；函数拒绝写入其他项目分块。
        - `chunks`：已经完成向量化的完整项目分块，类型为 `List[Dict[str, Any]]`。

    Returns:
        写入的分块数量。删除旧分块、COPY 新分块和索引生成处于同一事务：任一步
        失败都会回滚并保留旧索引，避免诊断读取半新半旧的数据。
    """
    if any(str(chunk.get("project") or "") != project for chunk in chunks):
        raise ValueError("批量替换分块包含其他项目数据")
    records = [
        (
            project,
            chunk["source_type"],
            chunk["source_id"],
            chunk.get("file_path"),
            chunk.get("line_start"),
            chunk.get("line_end"),
            chunk["content"],
            chunk["content_hash"],
            json.dumps(chunk.get("embedding", []), ensure_ascii=False),
            chunk.get("embedding_model"),
            chunk.get("embedding_dimension"),
            json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
        )
        for chunk in chunks
    ]
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM document_chunks WHERE project = $1",
                project,
            )
            if records:
                await connection.copy_records_to_table(
                    "document_chunks",
                    columns=[
                        "project",
                        "source_type",
                        "source_id",
                        "file_path",
                        "line_start",
                        "line_end",
                        "content",
                        "content_hash",
                        "embedding",
                        "embedding_model",
                        "embedding_dimension",
                        "metadata",
                    ],
                    records=records,
                )
    return len(records)


async def sync_document_chunks(
    pool: asyncpg.Pool, project: str, chunks: List[Dict[str, Any]]
) -> int:
    """增量同步项目分块，仅更新变化内容并清理已删除来源。

    参数:
        - `pool`：数据库连接池或测试替身，类型为 `asyncpg.Pool`。
        - `project`：项目标识，类型为 `str`。
        - `chunks`：待写入或待统计的文档分块集合，类型为 `List[Dict[str, Any]]`。
    """
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
