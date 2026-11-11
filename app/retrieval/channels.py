"""基于现有只读工具的检索通道适配器。"""

import asyncio
import re
from typing import Any, Callable, Dict, List

from app.retrieval.contracts import ChannelResult, RetrievalRequest, timed_channel_result


def _query_tokens(query: str) -> List[str]:
    """提取中英文检索词并保持顺序去重。

    参数:
        - `query`：混合检索关键词，长度已由调用方校验。

    Returns:
        英文符号片段和两字以上中文词，用于符号名称包含匹配。
    """
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_:]*|[\u4e00-\u9fff]{2,}", query.lower())
    return list(dict.fromkeys(words))


class CodeTextChannel:
    """封装代码全文精确检索。"""

    name = "code_text"

    def __init__(self, search: Callable[..., Dict[str, Any]]) -> None:
        """初始化代码文本通道。

        参数:
            - `search`：项目代码检索函数，必须支持 root、project、query 和
              max_results，并返回包含 `matches` 的字典。
        """
        self._search = search

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """在线程池中执行只读代码检索。

        参数:
            - `request`：代码检索请求和结果数量上限。
        """
        result = await asyncio.to_thread(
            self._search,
            request.root,
            request.project,
            request.query,
            max_results=request.limit,
        )
        hits = [
            {
                "source_type": "code",
                "source_id": f"{item['file']}:{item['line']}",
                "score": 1.0,
                "project": request.project,
                **item,
            }
            for item in result.get("matches") or []
        ]
        return ChannelResult(channel=self.name, hits=hits)


class SearchTermsChannel:
    """封装诊断首轮锚点和动态扩展词的一次批量代码扫描。"""

    name = "diagnostic_search_terms"

    def __init__(self, search: Callable[..., Dict[str, Any]]) -> None:
        """初始化诊断批量检索通道。

        参数:
            - `search`：支持 root、project、terms、max_results 和
              case_insensitive 的受控代码检索函数，返回包含 `matches` 的字典。
        """
        self._search = search

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """在同一批检索词上执行一次全仓扫描。

        参数:
            - `request`：诊断检索请求；`options["terms"]` 保存已去重检索词，
              `options["max_results"]` 和 `case_insensitive` 保存底层边界。

        Returns:
            原始代码命中集合。通道不排序、不判定业务事实，只提供可观测候选。
        """
        terms = list(request.options.get("terms") or [])
        max_results = int(request.options.get("max_results") or request.limit)
        case_insensitive = bool(request.options.get("case_insensitive", False))
        result = await asyncio.to_thread(
            self._search,
            request.root,
            request.project,
            terms,
            max_results=max_results,
            case_insensitive=case_insensitive,
        )
        return ChannelResult(channel=self.name, hits=list(result.get("matches") or []))


class SymbolChannel:
    """封装项目符号索引的本地名称匹配。"""

    name = "symbol_index"

    def __init__(self, index: Callable[..., Dict[str, Any]]) -> None:
        """初始化符号通道。

        参数:
            - `index`：项目符号索引函数，支持 root 和 project 并返回 symbols。
        """
        self._index = index

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """在线程池中构建或复用符号索引并筛选名称相关符号。

        参数:
            - `request`：符号检索请求；查询词用于名称包含匹配。
        """
        result = await asyncio.to_thread(self._index, request.root, request.project)
        tokens = _query_tokens(request.query) or [request.query.lower()]
        hits = []
        for symbol in result.get("symbols") or []:
            name = str(symbol.get("symbol_name") or "").lower()
            if any(token in name for token in tokens):
                hits.append(
                    {
                        "source_type": "symbol",
                        "source_id": f"{symbol['file']}:{symbol['line']}:{symbol['symbol_name']}",
                        "score": 0.9,
                        "project": request.project,
                        **symbol,
                    }
                )
        return ChannelResult(channel=self.name, hits=hits)


class HistoryChannel:
    """封装已完成诊断历史摘要检索。"""

    name = "history"

    def __init__(self, search: Callable[..., Any]) -> None:
        """初始化历史通道。

        参数:
            - `search`：异步历史检索函数，支持连接池、项目、关键词、分页和天数。
        """
        self._search = search

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """检索历史任务并返回已脱敏摘要。

        参数:
            - `request`：检索请求；`options` 必须包含 `pool`，可选
              `history_offset` 和 `history_days`。
        """
        pool = request.options.get("pool")
        hits = await self._search(
            pool,
            request.project,
            request.query,
            request.limit,
            int(request.options.get("history_offset") or 0),
            request.options.get("history_days"),
        )
        return ChannelResult(channel=self.name, hits=list(hits))


class DocumentChunkChannel:
    """封装 PostgreSQL 全文和向量兼容分块检索。"""

    name = "document_chunks"

    def __init__(self, search: Callable[..., Any]) -> None:
        """初始化文档分块通道。

        参数:
            - `search`：异步分块检索函数，支持连接池、项目、关键词和数量。
        """
        self._search = search

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """检索项目文档分块并归一化来源分数。

        参数:
            - `request`：检索请求；`options` 必须包含元数据库连接池。
        """
        rows = await self._search(
            request.options.get("pool"),
            request.project,
            request.query,
            request.limit,
        )
        hits = []
        for item in rows:
            normalized = dict(item)
            normalized["score"] = min(0.85, max(0.1, float(normalized.get("score") or 0.1)))
            hits.append(normalized)
        return ChannelResult(channel=self.name, hits=hits)
