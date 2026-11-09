"""PHP 符号查询工具。

这些工具在项目级索引上提供符号定义、引用和调用关系查询，供 Worker、
后续 Agent 编排器和管理后台复用。
"""

from collections import Counter
from typing import Dict, List, Optional

from app.php_index import index_repository, trace_call_chain


def find_symbol(root: str, project: str, symbol: str) -> Dict[str, List[Dict]]:
    """查找项目中名称精确匹配的类、方法或函数定义。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `symbol`：PHP 符号名称，类型为 `str`。
    """
    index = index_repository(root, project)
    matches = [item for item in index["symbols"] if item["symbol_name"] == symbol]
    return {"project": project, "symbol": symbol, "matches": matches, "total": len(matches)}


def find_references(root: str, project: str, symbol: str) -> Dict[str, List[Dict]]:
    """查找项目中指向指定符号的静态调用或实例调用。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `symbol`：PHP 符号名称，类型为 `str`。
    """
    index = index_repository(root, project)
    matches = [item for item in index["references"] if item["target"] == symbol]
    return {"project": project, "symbol": symbol, "matches": matches, "total": len(matches)}


def get_project_symbol_summary(root: str, project: str, limit: int = 100) -> Dict:
    """返回项目符号和引用摘要，限制结果数量避免报告过大。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
    """
    if limit < 1 or limit > 1000:
        raise ValueError("符号摘要数量必须在 1 到 1000 之间")
    index = index_repository(root, project)
    return summarize_symbol_index(index, project, limit)


def _relevant_symbol_score(
    item: Dict,
    query_terms: List[str],
    evidence_file_scores: Counter,
) -> int:
    """计算符号与当前问题的相关性分数，供有限报告上下文优先使用。

    参数:
        - `item`：项目符号索引中的单个类、方法或函数记录，类型为 `Dict`。
        - `query_terms`：已转为小写的业务词和代码符号，类型为 `List[str]`。
        - `evidence_file_scores`：代码检索命中的文件及次数，类型为 `Counter`。

    Returns:
        非负整数分数。名称直接命中当前问题最高，其次是与问题证据同文件，
        从未出现在问题或证据中的符号返回 0，避免全项目前 N 项挤占报告。
    """
    name = str(item.get("symbol_name") or "").lower()
    signature = str(item.get("signature") or "").lower()
    score = 0
    for term in query_terms:
        if term and term in name:
            score += 20
        elif term and term in signature:
            score += 8
    score += min(evidence_file_scores.get(str(item.get("file") or ""), 0), 5)
    return score


def summarize_symbol_index(
    index: Dict[str, List[Dict]],
    project: str,
    limit: int = 100,
    queries: Optional[List[str]] = None,
    evidence: Optional[List[Dict]] = None,
) -> Dict:
    """按当前问题和代码命中构建相关符号、引用、配置与 SQL 摘要。

    参数:
        - `index`：项目代码或符号索引，类型为 `Dict[str, List[Dict]]`。
        - `project`：项目标识，类型为 `str`。
        - `limit`：单次结果数量上限，类型为 `int`。
        - `queries`：从当前问题提取并扩展后的业务词和代码符号，类型为
          `Optional[List[str]]`；为空时保持旧版全项目摘要行为。
        - `evidence`：当前问题已经命中的代码证据，类型为 `Optional[List[Dict]]`；
          用于把同一业务文件中的类和方法提升到报告前部。

    Returns:
        包含 `project`、`symbols`、`references`、`configs` 和 `sql` 的字典。
        传入问题词或证据时，只返回与问题有名称、文件或调用关系的索引项，
        防止无关项目内容进入 LLM 上下文。
    """
    if limit < 1 or limit > 1000:
        raise ValueError("符号摘要数量必须在 1 到 1000 之间")
    if not queries and not evidence:
        return {
            "project": project,
            "symbols": index["symbols"][:limit],
            "references": index["references"][:limit],
            "configs": index["configs"][:limit],
            "sql": index["sql"][:limit],
        }

    query_terms = [str(item).strip().lower() for item in queries or [] if str(item).strip()]
    evidence_file_scores = Counter(
        str(item.get("file") or "")
        for item in evidence or []
        if item.get("file")
    )
    scored_symbols = []
    for item in index["symbols"]:
        score = _relevant_symbol_score(item, query_terms, evidence_file_scores)
        if score > 0:
            scored_symbols.append((score, item))
    scored_symbols.sort(
        key=lambda pair: (
            -pair[0],
            str(pair[1].get("file") or ""),
            int(pair[1].get("line") or 0),
        )
    )
    symbols = [item for _, item in scored_symbols[:limit]]
    selected_names = {str(item.get("symbol_name") or "") for item in symbols}
    selected_files = {
        str(item.get("file") or "") for item in symbols if item.get("file")
    }
    selected_files.update(
        file_name
        for file_name, _ in evidence_file_scores.most_common(20)
        if file_name
    )

    references = [
        item
        for item in index["references"]
        if str(item.get("file") or "") in selected_files
        or str(item.get("source") or "") in selected_names
        or str(item.get("target") or "") in selected_names
    ][:limit]
    configs = [
        item
        for item in index["configs"]
        if str(item.get("file") or "") in selected_files
        or any(term in str(item.get("key") or "").lower() for term in query_terms)
    ][:limit]
    sql = [
        item
        for item in index["sql"]
        if str(item.get("file") or "") in selected_files
        or any(term in str(item.get("content") or "").lower() for term in query_terms)
    ][:limit]
    return {
        "project": project,
        "symbols": symbols,
        "references": references,
        "configs": configs,
        "sql": sql,
    }


def select_call_chain_entries(
    index: Dict[str, List[Dict]],
    queries: List[str],
    evidence: List[Dict],
    limit: int = 8,
) -> List[str]:
    """从问题词和证据行位置选择最相关的调用链入口。

    参数:
        - `index`：项目代码或符号索引，类型为 `Dict[str, List[Dict]]`。
        - `queries`：从问题提取的类名、方法名和业务关键词，类型为 `List[str]`。
        - `evidence`：当前问题命中的代码行及其上下文，类型为 `List[Dict]`。
        - `limit`：最多返回的调用链入口数，类型为 `int`。

    Returns:
        按相关性排列的符号全名。先选择与问题词精确相关的类或方法，再选择
        命中代码行之前最近的方法；不会用全项目前 N 个符号伪造调用链。
    """
    if limit < 1 or limit > 20:
        raise ValueError("调用链入口数量必须在 1 到 20 之间")
    symbols = index.get("symbols") or []
    query_terms = [str(item).strip().lower() for item in queries if str(item).strip()]
    selected: List[str] = []

    def append(name: str) -> None:
        """去重追加一个已经通过索引确认的符号名。

        参数:
            - `name`：符号全名，类型为 `str`。
        """
        if name and name not in selected and len(selected) < limit:
            selected.append(name)

    # 先处理符号索引已经确认的完整类名或 `Class::method`。问题规划器会把
    # 发现的入口方法放在检索词列表中，如果先按 “withdraw” 等子串扫描，项目
    # 中更早声明的无关方法会占满名额，真正入口及其调用链反而无法出现。
    query_set = set(query_terms)
    symbols_by_name = {
        str(item.get("symbol_name") or "").lower(): str(item.get("symbol_name") or "")
        for item in symbols
    }
    for term in query_terms:
        if "::" not in term:
            continue
        append(symbols_by_name.get(term, ""))
        if len(selected) >= limit:
            return selected

    for term in query_terms:
        if "::" in term:
            continue
        append(symbols_by_name.get(term, ""))
        if len(selected) >= limit:
            return selected

    # 完整符号不足时，再接受问题文本中的叶子方法名或类名。最后才允许子串
    # 匹配，保证模糊业务词仍可发现符号，但不会抢在确认入口之前获得调用链名额。
    for item in symbols:
        name = str(item.get("symbol_name") or "")
        lowered = name.lower()
        leaf_name = lowered.rsplit("::", 1)[-1]
        if lowered in query_set or leaf_name in query_set:
            append(name)
        if len(selected) >= limit:
            return selected

    for item in symbols:
        name = str(item.get("symbol_name") or "")
        lowered = name.lower()
        if any(term in lowered for term in query_terms):
            append(name)
        if len(selected) >= limit:
            return selected

    symbols_by_file: Dict[str, List[Dict]] = {}
    for item in symbols:
        symbols_by_file.setdefault(str(item.get("file") or ""), []).append(item)
    for group in symbols_by_file.values():
        group.sort(key=lambda item: int(item.get("line") or 0))
    for evidence_item in evidence:
        file_name = str(evidence_item.get("file") or "")
        line_number = int(evidence_item.get("line") or 0)
        candidates = [
            item
            for item in symbols_by_file.get(file_name, [])
            if int(item.get("line") or 0) <= line_number
        ]
        if candidates:
            append(str(candidates[-1].get("symbol_name") or ""))
        if len(selected) >= limit:
            break
    return selected


__all__ = [
    "find_symbol",
    "find_references",
    "get_project_symbol_summary",
    "select_call_chain_entries",
    "summarize_symbol_index",
    "trace_call_chain",
]
