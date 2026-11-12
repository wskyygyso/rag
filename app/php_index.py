"""PHP 代码符号、引用和调用链分析工具。

当前实现使用受限正则扫描常见 PHP 声明和调用形式，目标是先建立可用的
符号关系基础；后续可以在不改变返回结构的前提下替换为 Tree-sitter AST。
"""

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.code_tools import CodeRepository
from app.repository_files import iter_repository_files

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

# 中文注释术语抽取：只识别包含汉字的注释，用于自动派生“中文业务词 → 代码标识”。
# 抽取结果只进入项目知识库，不参与调用链和诊断结论。
_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_COMMENT_PATTERN = re.compile(r"(?:\/\/|#|\/\*|\*\/|\*)\s*(.*)")
_GLOSSARY_TERM_SPLITTER = re.compile(r"[，。；：、,;:()（）\[\]【】\"'`|]+")
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\\]+")
_CONFIG_ARRAY_KEY_PATTERN = re.compile(
    r"^['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>"
)
# 单文件术语上限，避免超大注释文件把符号索引和知识库写入放大。
MAX_GLOSSARY_TERMS_PER_FILE = 200
# 中文术语长度范围：过短无法区分业务，过长无法作为检索词。
MIN_GLOSSARY_TERM_LENGTH = 2
MAX_GLOSSARY_TERM_LENGTH = 40
# 声明前允许回看的注释行数，覆盖常见的单行和多行文档注释。
GLOSSARY_COMMENT_LOOKBACK = 3


def _line_number(source: str, position: int) -> int:
    """根据字符串偏移量计算从 1 开始的源代码行号。

    参数:
        - `source`：PHP 源码文本，类型为 `str`。
        - `position`：源码字符位置，类型为 `int`。
    """
    return source.count("\n", 0, position) + 1


def _enclosing_class(classes: List[Tuple[int, str]], position: int) -> Optional[str]:
    """返回声明位置之前最近的类名，用于组装方法全限定名。

    参数:
        - `classes`：PHP 类声明及其源码位置，类型为 `List[Tuple[int, str]]`。
        - `position`：源码字符位置，类型为 `int`。
    """
    current = None
    for class_position, class_name in classes:
        if class_position > position:
            break
        current = class_name
    return current


def _enclosing_method(methods: List[Tuple[int, str]], position: int) -> Optional[str]:
    """返回调用位置之前最近的方法名，用于建立调用边的起点。

    参数:
        - `methods`：PHP 方法声明及其源码位置，类型为 `List[Tuple[int, str]]`。
        - `position`：源码字符位置，类型为 `int`。
    """
    current = None
    for method_position, method_name in methods:
        if method_position > position:
            break
        current = method_name
    return current


def _clean_glossary_term(value: str) -> str:
    """把注释片段清理为可检索的中文业务词。

    参数:
        - `value`：从注释中切分出的文本片段，类型为 `str`；可能包含英文、
          数字、标点或空白。

    Returns:
        只保留汉字和少量中文连接词后的文本，并截断到最大长度。空字符串表示
        该片段不足以构成业务术语，调用方应跳过。
    """
    text = str(value or "").strip(" \t*/-#")
    if not text or not _CHINESE_PATTERN.search(text):
        return ""
    # 注释里的英文标识符和数字会干扰术语匹配，直接移除；业务名称通常是连续
    # 汉字，移除后仍保持可读。
    text = _ASCII_TOKEN_PATTERN.sub("", text).strip()
    text = re.sub(r"\s+", "", text)
    if not (MIN_GLOSSARY_TERM_LENGTH <= len(text) <= MAX_GLOSSARY_TERM_LENGTH):
        return ""
    return text


def _comment_terms(raw_line: str) -> List[str]:
    """从一行 PHP 源码中提取中文注释术语。

    参数:
        - `raw_line`：PHP 源码单行，类型为 `str`。

    Returns:
        去重后的中文术语列表。注释内容按常见中文标点切分，只保留长度 2 到 40
        的片段；没有注释或注释不含汉字时返回空列表。
    """
    match = _COMMENT_PATTERN.search(raw_line)
    if match is None:
        return []
    comment = match.group(1)
    if not _CHINESE_PATTERN.search(comment):
        return []
    terms: List[str] = []
    for part in _GLOSSARY_TERM_SPLITTER.split(comment):
        cleaned = _clean_glossary_term(part)
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms


def _extract_glossary(source: str, file_path: str) -> List[Dict]:
    """从单个 PHP 文件抽取中文术语与真实代码标识的对应关系。

    参数:
        - `source`：PHP 文件全文，类型为 `str`。
        - `file_path`：项目内相对路径，类型为 `str`；用于生成证据引用和目录词。

    Returns:
        术语条目列表，字段包括 `term`、`code_term`、`term_kind`、`source`、
        `file`、`line` 和 `confidence`。只配对同一文件内真实出现的类名、方法名、
        配置键、目录名或文件名干，不猜测其他业务名；单文件最多返回
        `MAX_GLOSSARY_TERMS_PER_FILE` 条。
    """
    lines = source.splitlines()
    comment_terms: List[Tuple[int, str, str]] = []
    for index, raw_line in enumerate(lines):
        for term in _comment_terms(raw_line):
            comment_terms.append((index + 1, term, raw_line))
    if not comment_terms:
        return []

    entries: List[Dict] = []
    seen = set()

    def add(term: str, code_term: str, term_kind: str, line: int) -> None:
        """登记一条术语映射，并跳过重复或非法标识。

        参数:
            - `term`：中文业务词，类型为 `str`。
            - `code_term`：真实代码标识，类型为 `str`。
            - `term_kind`：代码词类型，类型为 `str`。
            - `line`：证据行号，类型为 `int`。

        Returns:
            无返回值；结果直接写入外层 `entries`。
        """
        code = str(code_term or "").strip()
        if not code or not term:
            return
        signature = (term, code)
        if signature in seen:
            return
        seen.add(signature)
        entries.append(
            {
                "term": term,
                "code_term": code,
                "term_kind": term_kind,
                "source": "comment" if term_kind != "runtime" else "llm",
                "file": file_path,
                "line": line,
                "confidence": 0.8,
            }
        )

    # 声明同行或前 3 行的中文注释与类名、方法名、函数名配对。
    declarations: List[Tuple[int, str]] = []
    for match in CLASS_PATTERN.finditer(source):
        declarations.append((_line_number(source, match.start()), match.group(2)))
    for match in METHOD_PATTERN.finditer(source):
        declarations.append((_line_number(source, match.start()), match.group(1)))
    for line_number, code_term in declarations:
        nearby = [
            (comment_line, term)
            for comment_line, term, _ in comment_terms
            if line_number - GLOSSARY_COMMENT_LOOKBACK <= comment_line <= line_number
        ]
        if nearby:
            # 只取离声明最近的一条中文注释，避免把文件头说明套到每个方法上。
            _, term = max(nearby, key=lambda item: item[0])
            add(term, code_term, "symbol", line_number)
        if len(entries) >= MAX_GLOSSARY_TERMS_PER_FILE:
            break

    # 配置键同行中文注释，例如 `'poolBase' => 900000, // 奖池基础值`。
    if len(entries) < MAX_GLOSSARY_TERMS_PER_FILE:
        for index, raw_line in enumerate(lines):
            key_match = _CONFIG_ARRAY_KEY_PATTERN.match(raw_line.strip())
            if key_match is None:
                continue
            terms = _comment_terms(raw_line)
            for term in terms:
                add(term, key_match.group("key"), "config_key", index + 1)
                if len(entries) >= MAX_GLOSSARY_TERMS_PER_FILE:
                    break
            if len(entries) >= MAX_GLOSSARY_TERMS_PER_FILE:
                break

    # 中文注释中直接提到目录名或文件名干时，建立路径级术语；目录名比通用
    # `config.php` 更能说明业务归属，所以目录词优先登记。
    if len(entries) < MAX_GLOSSARY_TERMS_PER_FILE:
        normalized_path = file_path.replace("\\", "/")
        directory_parts = normalized_path.split("/")[:-1]
        file_stem = normalized_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        for comment_line, term, raw_line in comment_terms:
            folded_raw = raw_line.casefold()
            for part in directory_parts:
                if part and part.casefold() in folded_raw:
                    add(term, part, "directory", comment_line)
            if file_stem and file_stem.casefold() in folded_raw:
                add(term, file_stem, "file", comment_line)
            if len(entries) >= MAX_GLOSSARY_TERMS_PER_FILE:
                break

    return entries[:MAX_GLOSSARY_TERMS_PER_FILE]


def parse_php_source(source: str, file_path: str) -> Dict[str, List[Dict]]:
    """解析 PHP 源码中的符号、调用关系、配置读取和 SQL 片段。

    参数:
        - `source`：PHP 文件全文，类型为 `str`。
        - `file_path`：源码在项目中的相对路径，用于写入证据来源，类型为 `str`。

    Returns:
        包含 `symbols`、`references`、`configs`、`sql` 和 `glossary` 五类结果的
        字典。`glossary` 只保存中文注释派生的术语，供项目知识库使用，不参与
        调用链和诊断结论。
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
    glossary = _extract_glossary(source, file_path)
    return {
        "symbols": symbols,
        "references": references,
        "configs": configs,
        "sql": sql,
        "glossary": glossary,
    }


def iter_php_files(repository: CodeRepository) -> Iterable[Path]:
    """遍历项目中可安全读取的 PHP 文件。

    参数:
        - `repository`：已验证的仓库对象，类型为 `CodeRepository`。扫描只读取
          项目白名单内的普通文件，不跟随符号链接。

    生成:
        PHP 文件路径。依赖、缓存、坏链和单个不可读目录会被跳过，不能中断其余
        源码的符号索引。
    """
    yield from iter_repository_files(repository.path, {".php"})


def index_repository(root: str, project: str) -> Dict[str, List[Dict]]:
    """扫描一个项目并合并所有 PHP 文件的符号和引用结果。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
    """
    repository = CodeRepository(root, project)
    result: Dict[str, List[Dict]] = {
        "symbols": [],
        "references": [],
        "configs": [],
        "sql": [],
        "glossary": [],
    }
    for path in iter_php_files(repository):
        relative_path = str(path.relative_to(repository.path))
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
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

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `entry`：调用链入口符号，类型为 `str`。
        - `depth`：调用链展开深度，类型为 `int`。
    """
    if depth < 1 or depth > 10:
        raise ValueError("调用链深度必须在 1 到 10 之间")
    index = index_repository(root, project)
    return trace_index_call_chain(index, entry, depth)


def trace_index_call_chain(
    index: Dict[str, List[Dict]],
    entry: str,
    depth: int = 5,
) -> Dict[str, List[Dict]]:
    """基于已有项目索引展开调用链，避免重复扫描 PHP 仓库。

    参数:
        - `index`：项目代码或符号索引，类型为 `Dict[str, List[Dict]]`。
        - `entry`：调用链入口符号，类型为 `str`。
        - `depth`：调用链展开深度，类型为 `int`。
    """
    if depth < 1 or depth > 10:
        raise ValueError("调用链深度必须在 1 到 10 之间")
    symbols = {item["symbol_name"]: item for item in index["symbols"]}
    all_edges = []
    for reference in index["references"]:
        all_edges.append({"from": reference.get("source"), "to": reference["target"], **reference})
    nodes: Dict[str, Dict] = {}
    reached_edges: List[Dict] = []
    seen_edges = set()
    queue = [(entry, 0)]
    while queue:
        current, current_depth = queue.pop(0)
        if current in nodes or current_depth > depth:
            continue
        nodes[current] = {"symbol_name": current, **symbols.get(current, {})}
        if current_depth == depth:
            continue
        for edge in all_edges:
            if edge["from"] == current and edge["to"] not in nodes:
                queue.append((edge["to"], current_depth + 1))
            if edge["from"] == current:
                edge_key = (edge["from"], edge["to"], edge["file"], edge["line"])
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    reached_edges.append(edge)
    return {"entry": entry, "nodes": list(nodes.values()), "edges": reached_edges}
