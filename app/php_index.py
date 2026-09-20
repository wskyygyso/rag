"""PHP 代码符号、引用和调用链分析工具。

当前实现使用受限正则扫描常见 PHP 声明和调用形式，目标是先建立可用的
符号关系基础；后续可以在不改变返回结构的前提下替换为 Tree-sitter AST。
"""

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.code_tools import CodeRepository

CLASS_PATTERN = re.compile(r"\b(class|interface|trait)\s+([A-Za-z_][A-Za-z0-9_]*)")
METHOD_PATTERN = re.compile(
    r"\b(?:(?:public|protected|private|static|final|abstract)\s+)*function\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)"
)
STATIC_CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)\s*\(")
THIS_CALL_PATTERN = re.compile(r"\$this\s*->\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
CONFIG_PATTERN = re.compile(
    r"\bConfig::(?:get|set)\s*\(\s*['\"]([^'\"]+)['\"]"
)
SQL_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\s+.+?\b(FROM|INTO|SET)\b", re.I)
IGNORED_CALLS = {"if", "for", "foreach", "while", "switch", "catch", "function", "echo"}


def _line_number(source: str, position: int) -> int:
    """根据字符串偏移量计算从 1 开始的源代码行号。"""
    return source.count("\n", 0, position) + 1


def _enclosing_class(classes: List[Tuple[int, str]], position: int) -> Optional[str]:
    """返回声明位置之前最近的类名，用于组装方法全限定名。"""
    current = None
    for class_position, class_name in classes:
        if class_position > position:
            break
        current = class_name
    return current


def _enclosing_method(methods: List[Tuple[int, str]], position: int) -> Optional[str]:
    """返回调用位置之前最近的方法名，用于建立调用边的起点。"""
    current = None
    for method_position, method_name in methods:
        if method_position > position:
            break
        current = method_name
    return current


def parse_php_source(source: str, file_path: str) -> Dict[str, List[Dict]]:
    """解析 PHP 源码中的符号、调用关系、配置读取和 SQL 片段。

    Args:
        source: PHP 文件全文。
        file_path: 文件相对路径，用于写入证据来源。

    Returns:
        包含 `symbols`、`references`、`configs` 和 `sql` 四类结果的字典。
    """
    classes = [
        (match.start(), match.group(2))
        for match in CLASS_PATTERN.finditer(source)
    ]
    classes.sort()
    method_positions: List[Tuple[int, str]] = []
    symbols: List[Dict] = []
    for match in CLASS_PATTERN.finditer(source):
        symbols.append({
            "symbol_type": match.group(1),
            "symbol_name": match.group(2),
            "file": file_path,
            "line": _line_number(source, match.start()),
            "signature": match.group(0),
        })
    for match in METHOD_PATTERN.finditer(source):
        class_name = _enclosing_class(classes, match.start())
        symbol_name = f"{class_name}::{match.group(1)}" if class_name else match.group(1)
        method_positions.append((match.start(), symbol_name))
        symbols.append({
            "symbol_type": "method" if class_name else "function",
            "symbol_name": symbol_name,
            "file": file_path,
            "line": _line_number(source, match.start()),
            "signature": match.group(0),
        })

    references: List[Dict] = []
    for match in STATIC_CALL_PATTERN.finditer(source):
        references.append({
            "reference_type": "static_call",
            "source": _enclosing_method(method_positions, match.start()),
            "target": f"{match.group(1)}::{match.group(2)}",
            "file": file_path,
            "line": _line_number(source, match.start()),
        })
    for match in THIS_CALL_PATTERN.finditer(source):
        class_name = _enclosing_class(classes, match.start())
        target = f"{class_name}::{match.group(1)}" if class_name else match.group(1)
        references.append({
            "reference_type": "this_call",
            "source": _enclosing_method(method_positions, match.start()),
            "target": target,
            "file": file_path,
            "line": _line_number(source, match.start()),
        })

    configs = [
        {
            "key": match.group(1),
            "file": file_path,
            "line": _line_number(source, match.start()),
        }
        for match in CONFIG_PATTERN.finditer(source)
    ]
    sql = [
        {
            "operation": match.group(1).upper(),
            "content": match.group(0).strip(),
            "file": file_path,
            "line": _line_number(source, match.start()),
        }
        for match in SQL_PATTERN.finditer(source)
    ]
    return {"symbols": symbols, "references": references, "configs": configs, "sql": sql}


def iter_php_files(repository: CodeRepository) -> Iterable[Path]:
    """遍历项目中的 PHP 文件，并跳过依赖和缓存目录。"""
    ignored = {".git", "vendor", "node_modules", "runtime", "storage", "cache"}
    for path in repository.path.rglob("*.php"):
        if path.is_file() and not any(part in ignored for part in path.parts):
            yield path


def index_repository(root: str, project: str) -> Dict[str, List[Dict]]:
    """扫描一个项目并合并所有 PHP 文件的符号和引用结果。"""
    repository = CodeRepository(root, project)
    result: Dict[str, List[Dict]] = {
        "symbols": [],
        "references": [],
        "configs": [],
        "sql": [],
    }
    for path in iter_php_files(repository):
        relative_path = str(path.relative_to(repository.path))
        source = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_php_source(source, relative_path)
        for key in result:
            result[key].extend(parsed[key])
    return result


def trace_call_chain(
    root: str,
    project: str,
    entry: str,
    depth: int = 5,
) -> Dict[str, List[Dict]]:
    """从指定入口沿静态调用关系展开调用链。

    未找到定义的外部目标仍会保留为边，但不会伪造目标文件和行号。
    """
    if depth < 1 or depth > 10:
        raise ValueError("调用链深度必须在 1 到 10 之间")
    index = index_repository(root, project)
    symbols = {item["symbol_name"]: item for item in index["symbols"]}
    edges = []
    for reference in index["references"]:
        edges.append({"from": reference.get("source"), "to": reference["target"], **reference})
    nodes: Dict[str, Dict] = {}
    queue = [(entry, 0)]
    while queue:
        current, current_depth = queue.pop(0)
        if current in nodes or current_depth > depth:
            continue
        nodes[current] = {"symbol_name": current, **symbols.get(current, {})}
        if current_depth == depth:
            continue
        for edge in edges:
            if edge["from"] == current and edge["to"] not in nodes:
                queue.append((edge["to"], current_depth + 1))
    return {"entry": entry, "nodes": list(nodes.values()), "edges": edges}
