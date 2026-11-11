"""受控代码检索工具。

本模块只允许访问配置的项目仓库，并对项目标识、文件路径、文件大小、
搜索结果数量和执行时间做限制，供 Agent 获取可审计的代码证据。
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.repository_files import IGNORED_REPOSITORY_PARTS, iter_repository_files


# `IGNORED_REPOSITORY_PARTS` 仍从本模块导出，兼容已有调用方；实际定义和安全
# 遍历实现位于 `app.repository_files`，避免各扫描入口各自维护排除规则。

# 中文问句里常见的提问外壳不应成为代码检索词。按长度从长到短替换，避免
# “哪些”先被“些”截断后留下不能表达业务含义的残余字符。
CHINESE_QUERY_NOISE = tuple(
    sorted(
        {
            "请问",
            "麻烦",
            "帮忙",
            "请检查",
            "检查一下",
            "排查一下",
            "排查",
            "分析一下",
            "在哪里",
            "哪里",
            "哪儿",
            "在哪",
            "是什么",
            "是多少",
            "根据",
            "依据",
            "基于",
            "有什么",
            "有哪些",
            "怎么做",
            "为什么",
            "如何",
            "是否",
            "能不能",
            "可不可以",
            "触发时",
            "触发",
            "时候",
            "当前",
            "项目",
            "具体",
            "相关",
            "业务",
            "功能",
            "限制",
            "影响",
            "问题",
            "需要",
            "可以",
            "多少",
            "一下",
            "怎么",
            "哪些",
            "什么",
            "哪个",
            "是否",
            "是",
            "的吗",
            "嘛",
            "呢",
            "吗",
            "的",
            "了",
            "时",
        },
        key=len,
        reverse=True,
    )
)

# 中文问句没有空格，仅靠连续中文正则会把“飞船商品碎片是如何分配的”当成一个
# 不可命中的长字符串。这里只在业务提问外壳处切开，得到业务对象和业务动作；
# 词边界仍来自用户原句，不依赖人工维护的领域词映射。
_QUESTION_SHELL_SPLIT_PATTERN = re.compile(
    r"如何|怎么|怎样|为什么|为啥|哪些|什么|是否|多少|是|吗|呢"
)
_YEAR_PATTERN = re.compile(r"(?<!\d)(\d{2})年")

class CodeToolError(ValueError):
    """代码工具请求非法或超出仓库白名单时抛出的业务异常。"""


class CodeRepository:
    """表示一个经过路径白名单校验的项目代码仓库。"""

    def __init__(self, root: str, project: str) -> None:
        """校验项目标识并解析项目根目录。

        参数:
            - `root`：所有项目仓库的根目录，类型为 `str`。
            - `project`：项目标识，对应根目录下的一级子目录，类型为 `str`。
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", project):
            raise CodeToolError("项目标识包含非法字符")
        base = Path(root).expanduser().resolve()
        repository = (base / project).resolve()
        if base not in repository.parents:
            raise CodeToolError("项目路径超出代码仓库白名单")
        if not repository.is_dir():
            raise CodeToolError(f"代码仓库不存在: {project}")
        self.base = base
        self.path = repository

    def safe_file(self, relative_path: str) -> Path:
        """将项目内相对路径解析为安全文件路径并阻止路径穿越。

        参数:
            - `relative_path`：项目内相对路径，类型为 `str`。
        """
        candidate = (self.path / relative_path).resolve()
        if self.path not in candidate.parents and candidate != self.path:
            raise CodeToolError("文件路径超出项目目录")
        if not candidate.is_file():
            raise CodeToolError(f"文件不存在: {relative_path}")
        return candidate


def read_file(
    root: str,
    project: str,
    relative_path: str,
    start_line: int = 1,
    end_line: int = 200,
    max_bytes: int = 512_000,
) -> Dict:
    """读取项目文件的指定行区间，并返回带行号的代码内容。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `relative_path`：项目内相对路径，类型为 `str`。
        - `start_line`：读取起始行，类型为 `int`。
        - `end_line`：读取结束行，类型为 `int`。
        - `max_bytes`：最大读取字节数，类型为 `int`。
    """
    if start_line < 1 or end_line < start_line or end_line - start_line > 1000:
        raise CodeToolError("行号范围无效，最多读取 1000 行")
    file_path = CodeRepository(root, project).safe_file(relative_path)
    if file_path.stat().st_size > max_bytes:
        raise CodeToolError("文件超过单次读取大小限制")
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1 : end_line]
    return {
        "project": project,
        "file": relative_path,
        "start_line": start_line,
        "end_line": min(end_line, len(lines)),
        "content": "\n".join(f"{start_line + index}: {line}" for index, line in enumerate(selected)),
    }


def _iter_text_files(repository: Path, extensions: Iterable[str]) -> Iterable[Path]:
    """遍历可检索的文本文件，跳过依赖、缓存和版本控制目录。

    参数:
        - `repository`：已验证的仓库对象，类型为 `Path`。
        - `extensions`：允许检索的文件扩展名，类型为 `Iterable[str]`。
    """
    allowed = set(extensions)
    yield from iter_repository_files(repository, allowed)


def _python_search(repository: Path, query: str, max_results: int, extensions: List[str]) -> List[Dict]:
    """在没有 ripgrep 时使用 Python 执行大小写不敏感的文本搜索。

    参数:
        - `repository`：已验证的仓库对象，类型为 `Path`。
        - `query`：检索或查询文本，类型为 `str`。
        - `max_results`：最大结果数量，类型为 `int`。
        - `extensions`：允许检索的文件扩展名，类型为 `List[str]`。
    """
    matches: List[Dict] = []
    for path in _iter_text_files(repository, extensions):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if query.lower() in line.lower():
                matches.append({
                    "file": str(path.relative_to(repository)),
                    "line": line_number,
                    "content": line.strip(),
                })
                if len(matches) >= max_results:
                    return matches
    return matches


def search_code(
    root: str,
    project: str,
    query: str,
    max_results: int = 20,
    extensions: Optional[List[str]] = None,
) -> Dict:
    """在项目仓库中搜索固定字符串并返回文件、行号和匹配内容。

    参数:
        - `root`：仓库根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `query`：检索或查询文本，类型为 `str`。
        - `max_results`：最大结果数量，类型为 `int`。
        - `extensions`：允许检索的文件扩展名，类型为 `Optional[List[str]]`。
    """
    query = query.strip()
    if not query or len(query) > 200:
        raise CodeToolError("搜索关键词不能为空且不能超过 200 个字符")
    if max_results < 1 or max_results > 100:
        raise CodeToolError("搜索结果数量必须在 1 到 100 之间")
    repository = CodeRepository(root, project)
    extensions = extensions or [".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json"]
    matches: List[Dict] = []
    rg = shutil.which("rg")
    if rg:
        globs = [item if item.startswith("*") else f"*{item}" for item in extensions]
        command = [
            rg,
            "--no-follow",
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-count",
            str(max_results),
        ]
        for pattern in globs:
            command.extend(["--glob", pattern])
        # ripgrep 的 glob 规则后出现者优先；排除规则必须放在扩展名白名单之后，
        # 否则 `*.php` 会重新包含 runtime/vendor 等目录。
        for directory in sorted(IGNORED_REPOSITORY_PARTS):
            command.extend(["--glob", f"!**/{directory}/**"])
        command.extend(["-F", query, str(repository.path)])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodeToolError("代码搜索超时") from exc
        if completed.returncode not in (0, 1):
            raise CodeToolError(f"代码搜索失败: {completed.stderr.strip()}")
        for line in completed.stdout.splitlines()[:max_results]:
            file_name, line_number, content = line.split(":", 2)
            matches.append({
                "file": str(Path(file_name).relative_to(repository.path)),
                "line": int(line_number),
                "content": content.strip(),
            })
    else:
        matches = _python_search(repository.path, query, max_results, extensions)
    return {"project": project, "query": query, "matches": matches, "total": len(matches)}


def _append_unique_query(result: List[str], value: str, max_queries: int) -> bool:
    """把有效检索词追加到结果中，并判断是否已经达到数量上限。

    参数:
        - `result`：当前按优先级排列的检索词，类型为 `List[str]`。
        - `value`：准备追加的业务词、类名、方法名或配置键，类型为 `str`。
        - `max_queries`：单次问题允许生成的最大检索词数，类型为 `int`。

    Returns:
        达到上限时返回 `True`，调用方应停止继续扩展业务概念。
    """
    normalized = value.strip()
    if normalized and normalized not in result:
        result.append(normalized)
    return len(result) >= max_queries


def _clean_chinese_query(fragment: str) -> str:
    """移除中文问句中的提问外壳，只保留可检索的业务对象或业务动作。

    参数:
        - `fragment`：正则从问题中截取的连续中文片段，类型为 `str`。

    Returns:
        去除停用短语后的中文业务词；只剩单字或空字符串时返回空文本，避免
        把“的”“时”等无检索价值字符送到代码索引。
    """
    cleaned = fragment
    for noise in CHINESE_QUERY_NOISE:
        cleaned = cleaned.replace(noise, "")
    return cleaned if len(cleaned) >= 2 else ""


def _normalize_question_years(question: str) -> str:
    """把业务问题中的两位年份归一化为便于精确检索的四位年份。

    参数:
        - `question`：用户提交的原始问题，类型为 `str`。例如“25年年度金主”
          通常指 2025 年，而代码路径、配置文案和时间字段使用四位年份。

    Returns:
        两位年份已补全为 `20xx` 的问题文本，并折叠“2025年年度”这类重复表达。
        该函数只做展示层归一化，不会把年份当成业务事实。
    """

    def replace_year(match: re.Match) -> str:
        """补全单个两位年份，保留超出常见 20xx 范围的原文。

        参数:
            - `match`：`_YEAR_PATTERN` 命中的“25年”等年份片段，类型为
              `re.Match`。

        Returns:
            `20xx年` 形式的年份；若两位数不在 20 至 99 之间，则返回原片段。
        """
        short_year = int(match.group(1))
        if 20 <= short_year <= 99:
            return f"20{short_year:02d}年"
        return match.group(0)

    normalized = _YEAR_PATTERN.sub(replace_year, question)
    return normalized.replace("年年度", "年度")


def _split_chinese_clauses(fragment: str) -> List[str]:
    """按中文提问外壳拆分连续片段，同时保留可检索的业务对象和动作。

    参数:
        - `fragment`：问题中的连续中文片段，类型为 `str`。该片段可能同时包含
          业务名词、判断词和动词，例如“飞船商品碎片是如何分配的”。

    Returns:
        去重后的非空业务子句，例如“飞船商品碎片”和“分配”。拆分依据是中文
        提问结构，不依赖项目领域词典，因此未知业务问题也能直接复用。
    """
    result: List[str] = []
    for part in _QUESTION_SHELL_SPLIT_PATTERN.split(fragment):
        cleaned = _clean_chinese_query(part)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def extract_search_queries(question: str, max_queries: int = 18) -> List[str]:
    """从用户问题提取可命中的显式符号和中文业务关键词。

    参数:
        - `question`：用户提交的诊断问题，类型为 `str`。
        - `max_queries`：问题中提取的最大检索词数，类型为 `int`。

    Returns:
        按问题出现顺序去重的检索词。显式代码符号优先保留；中文业务词只去掉
        “有哪些”“根据”等提问外壳，再按业务对象和动作拆分，不读取项目领域映射。
        具体类、方法和配置由通用检索、概念扩展和符号索引发现。
    """
    normalized_question = _normalize_question_years(question)
    candidates = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
        r"|[A-Za-z_][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*"
        r"|[A-Za-z_][A-Za-z0-9_]{2,}"
        r"|[\u4e00-\u9fff]{2,}",
        normalized_question,
    )
    result: List[str] = []
    for candidate in candidates:
        if re.fullmatch(r"[\u4e00-\u9fff]+", candidate):
            domain_term = _clean_chinese_query(candidate)
            if not domain_term:
                continue
            # 保留清洗后的完整业务短语用于命中中文注释，再拆出业务对象和动作
            # 供通用检索。这里不把任何完整问题绑定到项目符号。
            if _append_unique_query(result, domain_term, max_queries):
                break
            for clause in _split_chinese_clauses(candidate):
                if _append_unique_query(result, clause, max_queries):
                    break
        elif _append_unique_query(result, candidate, max_queries):
            break
        if len(result) >= max_queries:
            break
    return result or [question[:200]]


def extract_generic_anchors(question: str, max_anchors: int = 24) -> List[str]:
    """从任意中文业务问题生成不依赖领域映射的通用检索片段。

    参数:
        - `question`：用户提交的原始项目问题，类型为 `str`。
        - `max_anchors`：单次通用召回允许生成的最大片段数，类型为 `int`。

    Returns:
        先保留提问结构切出的完整业务对象和动作，再补充两字业务词和长片段。
        长片段用于命中完整注释，两字动作用于命中“分配”“碎片”这类核心实现，
        不会被长句窗口挤出数量上限；调用方仍必须使用命中源码验证，不能把片段
        本身当成业务事实。
    """
    if max_anchors < 1 or max_anchors > 100:
        raise CodeToolError("通用检索片段数量必须在 1 到 100 之间")
    normalized_question = _normalize_question_years(question)
    fragments = re.findall(r"[\u4e00-\u9fff]{2,}", normalized_question)
    candidates: List[str] = []
    seen = set()

    def add_candidate(value: str) -> None:
        """把尚未出现的非空业务片段加入通用召回列表。

        参数:
            - `value`：准备加入的完整业务子句、两字词或中文 n-gram，类型为
              `str`；空值和重复值会被忽略。
        """
        if len(value) >= 2 and value not in seen:
            seen.add(value)
            candidates.append(value)

    clauses: List[str] = []
    for fragment in fragments:
        cleaned = _clean_chinese_query(fragment)
        if len(cleaned) < 2:
            continue
        for clause in _split_chinese_clauses(fragment):
            if clause not in clauses:
                clauses.append(clause)
            add_candidate(clause)
    ordered_clauses = sorted(clauses, key=len, reverse=True)
    # 先保留四字业务复合词（如“商品碎片”“年度金主”），再保留“飞船”“碎片”
    # 这类两字核心词。刻意不再枚举所有三字及更长窗口：嵌套 n-gram 会让一个
    # 泛化活动标题同时命中七八个词并压过真正实现文件，增加召回不等于增加精度。
    for clause in ordered_clauses:
        for start in range(0, len(clause) - 3):
            add_candidate(clause[start:start + 4])
    for clause in ordered_clauses:
        for start in range(0, len(clause) - 1):
            add_candidate(clause[start:start + 2])
    return candidates[:max_anchors]


def _generic_search_score(item: Dict) -> tuple:
    """为尚未进入规划器的通用命中生成稳定的源码优先排序键。

    参数:
        - `item`：批量检索命中的单行结果，包含项目内文件路径、源码行号和实际
          命中的中文片段，类型为 `Dict`。

    Returns:
        供升序排序的元组。信息更完整的业务片段、生产 PHP 目录和同时命中多个
        片段的行优先；后台页面、文档、测试和语言包降权。这里只做召回排序，
        不能据此判断业务结论，最终事实仍须由精确符号搜索和源码上下文验证。
    """
    file_name = str(item.get("file") or "")
    lowered = f"/{file_name.casefold()}"
    matched = [str(value) for value in item.get("matched_queries") or [] if str(value)]
    longest = max((len(value) for value in matched), default=0)
    term_score = longest * 6 + sum(min(len(value), 10) for value in matched) * 2
    location_score = 0
    if "miyouactivity/crontab/" in lowered:
        location_score += 34
    elif "miyouactivity/action_activity/" in lowered:
        location_score += 30
    elif "miyouactivity/logic/" in lowered or "miyouactivity/config/" in lowered:
        location_score += 28
    elif "/php/domain/" in lowered or "/php/services/" in lowered:
        location_score += 24
    elif "/php/action/" in lowered or "/php/model/" in lowered:
        location_score += 20
    elif "/php/conf/" in lowered or "/php/config/" in lowered:
        location_score += 18
    elif "/php/" in lowered:
        location_score += 12
    if "/docs/" in lowered or lowered.endswith(".md"):
        location_score += 2
    if "/reports/" in lowered:
        location_score -= 8
    if "/miyouadmin/" in lowered or "/admin/" in lowered:
        location_score -= 6
    if "/phpunit/" in lowered or "/tests/" in lowered:
        location_score -= 24
    if "/lang/" in lowered:
        location_score -= 12
    return (
        -(term_score + location_score),
        -len(matched),
        file_name,
        int(item.get("line") or 0),
    )


def search_code_terms(
    root: str,
    project: str,
    queries: Iterable[str],
    max_results: int = 120,
    extensions: Optional[Iterable[str]] = None,
    case_insensitive: bool = False,
    include_globs: Optional[Iterable[str]] = None,
) -> Dict:
    """一次检索多个通用业务片段并返回去重后的代码和文档命中。

    参数:
        - `root`：所有项目仓库的白名单根目录，类型为 `str`。
        - `project`：当前诊断项目标识，类型为 `str`。
        - `queries`：由问题自动生成的业务片段，类型为 `Iterable[str]`。
        - `max_results`：单次通用召回保留的命中总数，类型为 `int`。
        - `extensions`：允许搜索的源码、配置和文档扩展名；为空时使用业务文档
          白名单且排除依赖、缓存和生成索引目录，类型为 `Optional[Iterable[str]]`。
        - `case_insensitive`：是否忽略英文大小写，类型为 `bool`。中文通用锚点使用
          默认精确匹配；跨语言概念词需要忽略大小写，才能让 `diamond` 命中
          `DiamondControl` 和 `getDiamondGears()`。
        - `include_globs`：可选的仓库相对路径白名单，例如
          `("**/config/**", "**/conf/**")`，类型为 `Optional[Iterable[str]]`。
          该参数用于从配置调用点返回定义文件读取真实值，防止大量业务代码命中
          占满全局结果预算；传入空值时保持全仓检索行为。

    Returns:
        包含 `matches` 的字典；每项附带实际命中的通用片段，供后续排序和审计。
        ripgrep 不可用时退回一次遍历的 Python 批量搜索，最终结果仍受数量上限约束。
    """
    if max_results < 1 or max_results > 500:
        raise CodeToolError("通用检索结果数量必须在 1 到 500 之间")
    terms: List[str] = []
    for query in queries:
        value = str(query).strip()
        if not value or len(value) > 200 or value in terms:
            continue
        terms.append(value)
    if not terms:
        return {"project": project, "queries": [], "matches": [], "total": 0}
    if len(terms) > 50:
        raise CodeToolError("单次通用检索片段不能超过 50 个")
    path_globs: List[str] = []
    for value in include_globs or []:
        pattern = str(value).strip().replace("\\", "/")
        if not pattern or len(pattern) > 200 or "\x00" in pattern or "\n" in pattern:
            raise CodeToolError("检索路径白名单格式无效")
        if pattern not in path_globs:
            path_globs.append(pattern)
    if len(path_globs) > 8:
        raise CodeToolError("检索路径白名单不能超过 8 项")

    repository = CodeRepository(root, project)
    allowed_extensions = list(
        extensions
        or [
            ".php",
            ".inc",
            ".phtml",
            ".conf",
            ".ini",
            ".yaml",
            ".yml",
            ".json",
            ".md",
            ".txt",
        ]
    )
    matches: List[Dict] = []
    rg = shutil.which("rg")
    if rg:
        # `--max-count` 是 ripgrep 的单文件上限，不是全局结果上限。年度活动配置
        # 可能在同一个文件的多个等级段重复业务字段，40 行仍能覆盖多级规则，同时
        # 防止超大脚本垄断结果；统一解析后再按片段长度和源码目录排序。
        command = [
            rg,
            "--no-follow",
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-count",
            str(min(40, max_results)),
        ]
        if case_insensitive:
            command.append("--ignore-case")
        for extension in allowed_extensions:
            pattern = extension if extension.startswith("*") else f"*{extension}"
            command.extend(["--glob", pattern])
        for directory in sorted(IGNORED_REPOSITORY_PARTS):
            command.extend(["--glob", f"!**/{directory}/**"])
        # 配置定义补查只允许查看调用方指定的目录范围。rg 的 glob 后出现者优先，
        # 先用全部允许扩展名排除，再追加目录白名单，才能形成真正的交集而不是
        # 让 `--glob` 白名单与扩展名白名单按并集匹配全部源码。最后仍保留生成目录
        # 排除规则，避免缓存文件通过白名单再次混入。
        if path_globs:
            for extension in allowed_extensions:
                negative_pattern = (
                    f"!**/*{extension}"
                    if extension.startswith(".")
                    else f"!**/{extension}"
                )
                command.extend(["--glob", negative_pattern])
        for pattern in path_globs:
            command.extend(["--glob", pattern])
        command.append("-F")
        for term in terms:
            command.extend(["-e", term])
        command.append(str(repository.path))
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodeToolError("通用代码检索超时") from exc
        if completed.returncode not in (0, 1):
            raise CodeToolError(f"通用代码检索失败: {completed.stderr.strip()}")
        seen = set()
        for line in completed.stdout.splitlines():
            file_name, line_number, content = line.split(":", 2)
            key = (file_name, int(line_number))
            if key in seen:
                continue
            seen.add(key)
            folded = content.casefold()
            matches.append(
                {
                    "file": str(Path(file_name).relative_to(repository.path)),
                    "line": int(line_number),
                    "content": content.strip(),
                    "matched_queries": [
                        term
                        for term in terms
                        if (
                            term.casefold() in folded
                            if case_insensitive
                            else term in content
                        )
                    ],
                }
            )
    else:
        # 镜像不保证安装 ripgrep。备用路径必须一次遍历文件并同时匹配全部
        # 业务片段；若逐词重复遍历整个大型仓库，24 个中文片段会产生 24 次磁盘
        # 扫描并导致诊断超时。每个文件沿用 ripgrep 的 40 行上限，防止单个巨型
        # 文档垄断结果，同时保证不同目录都有机会进入最终排序。
        folded_terms = [(term, term.casefold()) for term in terms]
        for path in _iter_text_files(repository.path, allowed_extensions):
            relative_file = path.relative_to(repository.path).as_posix()
            if path_globs and not any(
                Path(relative_file).match(pattern) for pattern in path_globs
            ):
                continue
            try:
                if path.stat().st_size > 5_000_000:
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            file_hit_count = 0
            for line_number, line in enumerate(lines, start=1):
                folded = line.casefold()
                matched = [
                    term
                    for term, term_folded in folded_terms
                    if (
                        term_folded in folded
                        if case_insensitive
                        else term in line
                    )
                ]
                if not matched:
                    continue
                matches.append(
                    {
                        "file": str(path.relative_to(repository.path)),
                        "line": line_number,
                        "content": line.strip(),
                        "matched_queries": matched,
                    }
                )
                file_hit_count += 1
                if file_hit_count >= 40:
                    break
    matches.sort(key=_generic_search_score)
    matches = matches[:max_results]
    return {
        "project": project,
        "queries": terms,
        "matches": matches,
        "total": len(matches),
    }
