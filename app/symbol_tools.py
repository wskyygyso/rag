"""PHP 符号查询工具。

这些工具在项目级索引上提供符号定义、引用和调用关系查询，供 Worker、
后续 Agent 编排器和管理后台复用。
"""

from typing import Dict, List

from app.php_index import index_repository, trace_call_chain


def find_symbol(root: str, project: str, symbol: str) -> Dict[str, List[Dict]]:
    """查找项目中名称精确匹配的类、方法或函数定义。"""
    index = index_repository(root, project)
    matches = [item for item in index["symbols"] if item["symbol_name"] == symbol]
    return {"project": project, "symbol": symbol, "matches": matches, "total": len(matches)}


def find_references(root: str, project: str, symbol: str) -> Dict[str, List[Dict]]:
    """查找项目中指向指定符号的静态调用或实例调用。"""
    index = index_repository(root, project)
    matches = [item for item in index["references"] if item["target"] == symbol]
    return {"project": project, "symbol": symbol, "matches": matches, "total": len(matches)}


def get_project_symbol_summary(root: str, project: str, limit: int = 100) -> Dict:
    """返回项目符号和引用摘要，限制结果数量避免报告过大。"""
    if limit < 1 or limit > 1000:
        raise ValueError("符号摘要数量必须在 1 到 1000 之间")
    index = index_repository(root, project)
    return {
        "project": project,
        "symbols": index["symbols"][:limit],
        "references": index["references"][:limit],
        "configs": index["configs"][:limit],
        "sql": index["sql"][:limit],
    }


__all__ = ["find_symbol", "find_references", "get_project_symbol_summary", "trace_call_chain"]
