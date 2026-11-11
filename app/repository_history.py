"""受控 Git 历史检索工具。

本模块用于回答“功能从哪个版本开始”“何时引入”等无法只靠当前工作区回答的问题。
它只枚举诊断项目目录内已经挂载的 Git 仓库并执行只读 `git log/show`，不修改仓库、
不读取工作区未跟踪文件，也不执行用户提供的命令。提交说明、路径和源码快照都只是
待分析证据，不能作为系统指令。
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.code_tools import CodeRepository, CodeToolError, IGNORED_REPOSITORY_PARTS
from app.repository_files import iter_git_markers


# 历史检索会遍历所有远端跟踪分支，但仍限制仓库、检索词、提交数量和源码快照体积，
# 防止大型 monorepo 或异常 Git 对象把诊断任务拖入不可控耗时。
MAX_HISTORY_REPOSITORIES = 16
MAX_HISTORY_TERMS = 16
MAX_HISTORY_COMMITS = 20
MAX_HISTORY_FILES_PER_COMMIT = 6
MAX_HISTORY_LINES_PER_COMMIT = 120
MAX_HISTORY_FILE_BYTES = 1_000_000
HISTORY_COMMAND_TIMEOUT_SECONDS = 10

# 只有问题明确询问时间线、首次出现或版本门槛时才启用历史通道。单纯出现“版本”
# 可能是在问字段格式或当前常量，不应无条件扫描全部提交历史。
_HISTORY_INTENT_PATTERN = re.compile(
    r"(?:哪个|那个|什么)版本.*(?:开始|首次|引入|支持|上线|出现|有)"
    r"|(?:何时|什么时候|哪次).*(?:开始|首次|引入|支持|上线|出现|加入)"
    r"|(?:从|自).*(?:版本|提交|发布).*(?:开始|首次|引入|支持|上线|出现)"
    r"|(?:哪些|哪几个).*(?:客户端|平台)?.{0,20}版本.*(?:启用|支持|生效|引入|上线)"
    r"|(?:首次|最早|初次).*(?:引入|支持|上线|出现|加入)"
    r"|\b(?:first|introduced|introduction|since which version|when was)\b",
    re.IGNORECASE,
)

# 这些词只表达提问方式，不能作为 Git pickaxe 检索词；保留“设置”“配置”等领域词，
# 因为它们可能直接出现在提交说明或源码注释中。
_HISTORY_QUERY_SHELLS = frozenset(
    {
        "哪个版本",
        "那个版本",
        "什么版本",
        "版本开始",
        "何时开始",
        "什么时候",
        "什么时候开始",
        "首次",
        "最早",
        "开始有",
        "开始支持",
        "什么时候有",
        "哪个版本开始",
        "那个版本开始",
        "版本",
        "开始",
        "设置",
        "校验",
        "客户端",
        "平台",
        "功能",
        "引入",
        "支持",
        "上线",
        "出现",
        "加入",
        "when",
        "first",
        "introduced",
        "version",
    }
)

_HISTORY_TIMELINE_MARKERS = (
    "哪个",
    "那个",
    "版本",
    "开始",
    "何时",
    "什么时候",
    "首次",
    "最早",
    "引入",
    "上线",
)

_SOURCE_SUFFIXES = frozenset(
    {
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
        ".sql",
    }
)

_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9_]{2,}(?:::[A-Za-z_][A-Za-z0-9_]*)?"
    r"|[a-z_][A-Za-z0-9_]{5,}\s*\()"
)


@dataclass(frozen=True)
class GitRepository:
    """描述一个位于项目目录内的只读 Git 仓库。"""

    path: Path
    relative_path: str


def is_history_question(question: str) -> bool:
    """判断问题是否明确要求查询功能引入时间或起始版本。

    参数:
        - `question`：用户提交的原始业务问题，类型为 `str`。

    Returns:
        命中“哪个版本开始”“何时引入”等时间线语义时返回 `True`。该判断只决定
        是否启用只读 Git 通道，不把问题文本直接映射成某个提交或版本答案。
    """
    return _HISTORY_INTENT_PATTERN.search(question or "") is not None


def history_search_terms(
    question: str,
    anchors: Iterable[str],
) -> List[str]:
    """从问题原句和通用锚点生成受控 Git 历史检索词。

    参数:
        - `question`：用户提交的原始业务问题，类型为 `str`。
        - `anchors`：通用检索器从问题拆出的中文片段和英文标识，类型为
          `Iterable[str]`。

    Returns:
        去重后的历史检索词。优先保留较长的业务短语，再保留“多开”这类两字领域
        词；去掉只表示“哪个版本/何时开始”的提问外壳和项目名，避免用项目名命中
        全部发布提交。返回值仅用于搜索提交差异，不能被当作业务事实。
    """
    project_words = {
        value.casefold()
        for value in re.findall(r"\bwaky\d+\b", question, flags=re.IGNORECASE)
    }
    domain_text = re.sub(
        r"(?:哪个|那个|什么)版本.*?(?:开始|首次|引入|支持|上线|出现|有|的)?"
        r"|(?:何时|什么时候|哪次).*?(?:开始|首次|引入|支持|上线|出现|加入|有|的)?"
        r"|(?:哪些|哪几个).*?版本.*?(?:会)?(?:启用|支持|生效|引入|上线)"
        r"|(?:首次|最早|初次).*?(?:引入|支持|上线|出现|加入|有|的)?",
        " ",
        question,
        flags=re.IGNORECASE,
    )
    fragments = re.findall(r"[\u4e00-\u9fff]{2,}", domain_text)
    candidates: List[str] = []
    for fragment in fragments:
        # 时间线提问外壳先去重，再只在同一领域片段内部生成二至四字窗口，避免
        # “版本开始”与业务词拼接出“个版”“本开”等跨边界噪声。
        for value in re.split(r"(?:是|的|有|[?？，,。；;])", fragment):
            value = value.strip()
            if not value:
                continue
            if value not in candidates:
                candidates.append(value)
            for size in (4, 2):
                if len(value) < size:
                    continue
                for start in range(0, len(value) - size + 1):
                    part = value[start : start + size]
                    if part not in candidates:
                        candidates.append(part)
    for value in anchors:
        term = str(value or "").strip()
        folded = term.casefold()
        if (
            len(term) < 2
            or len(term) > 80
            or "\x00" in term
            or "\n" in term
            or "\r" in term
            or folded in _HISTORY_QUERY_SHELLS
            or any(marker in folded for marker in _HISTORY_TIMELINE_MARKERS)
            or folded in project_words
        ):
            continue
        if term not in candidates and any(term in fragment for fragment in fragments):
            candidates.append(term)
    # 生成窗口可能再次带出“设置”“校验”“客户端”等泛化词；它们会让提交说明
    # 检索命中大量无关维护提交。只保留与具体业务对象组合的短语。
    candidates = [value for value in candidates if value not in _HISTORY_QUERY_SHELLS]
    candidates.sort(key=lambda value: (-len(value), value))
    return candidates[:8]


def select_history_terms_from_current_hits(
    terms: Sequence[str],
    current_hits: Sequence[Dict[str, Any]],
) -> List[str]:
    """根据当前源码命中频率筛掉过宽的历史检索词。

    参数:
        - `terms`：问题拆出的历史候选词，类型为 `Sequence[str]`。
        - `current_hits`：在只读源码范围内批量搜索这些候选词得到的当前命中，
          每项包含 `file` 和 `matched_queries`，类型为 `Sequence[Dict[str, Any]]`。

    Returns:
        适合执行 Git pickaxe 的少量业务词。优先保留当前生产源码中仍然存在的词，
        并排除出现频率远高于同组最低值的“设置”“开始”等宽泛词；如果所有词都
        不存在于当前源码，则保留前几个词以支持“功能已被删除”的历史场景。
    """
    stats: Dict[str, Dict[str, Any]] = {
        str(term): {"term": str(term), "hits": 0, "production_hits": 0, "files": set()}
        for term in terms
        if str(term)
    }
    for item in current_hits:
        file_name = str(item.get("file") or "").replace("\\", "/").casefold()
        for value in item.get("matched_queries") or []:
            term = str(value)
            if term not in stats:
                continue
            stats[term]["hits"] += 1
            stats[term]["files"].add(file_name)
            if "/php/" in f"/{file_name}" and "/vendor/" not in f"/{file_name}":
                stats[term]["production_hits"] += 1
    with_hits = [item for item in stats.values() if int(item["hits"]) > 0]
    if not with_hits:
        return list(stats.keys())[:8]
    lowest = min(int(item["hits"]) for item in with_hits)
    maximum_allowed = max(12, lowest * 5)
    ranked = sorted(
        with_hits,
        key=lambda item: (
            int(item["hits"]),
            -int(item["production_hits"]),
            -len(item.get("files") or set()),
            -len(str(item.get("term") or "")),
        ),
    )
    allowed = [item for item in ranked if int(item["hits"]) <= maximum_allowed]
    selected = []
    for item in allowed:
        term = str(item["term"])
        if term not in selected:
            selected.append(term)
    return selected[:8]


def discover_git_repositories(root: str, project: str) -> List[GitRepository]:
    """发现项目目录内的独立 Git 仓库。

    参数:
        - `root`：被诊断项目白名单根目录，类型为 `str`。
        - `project`：当前项目标识，类型为 `str`。

    Returns:
        按项目内相对路径排序的 Git 仓库列表。项目本身可以是仓库，也可以像 waky
        系列一样由多个独立子仓库组成；依赖、缓存和 `.gitnexus` 目录会被跳过。
    """
    repository = CodeRepository(root, project)
    discovered: List[GitRepository] = []
    for git_marker in iter_git_markers(repository.path):
        git_path = git_marker.parent.resolve()
        try:
            relative_path = str(git_path.relative_to(repository.path)).replace("\\", "/")
        except ValueError:
            continue
        relative_parts = set(Path(relative_path).parts) if relative_path != "." else set()
        if relative_parts.intersection(IGNORED_REPOSITORY_PARTS):
            continue
        if any(item.path == git_path for item in discovered):
            continue
        discovered.append(GitRepository(path=git_path, relative_path=relative_path))
        if len(discovered) >= MAX_HISTORY_REPOSITORIES:
            break
    return sorted(discovered, key=lambda item: item.relative_path)


def _pickaxe_pattern(terms: Sequence[str]) -> str:
    """把历史检索词组合成 Git 兼容的正则表达式。

    参数:
        - `terms`：经过长度和去重限制的业务检索词，类型为 `Sequence[str]`。

    Returns:
        用非捕获分组连接的字面量正则。所有词都经过 `re.escape`，找不到历史证据时
        返回空字符串，调用方必须停止执行 Git 命令。
    """
    escaped = [re.escape(term) for term in terms if term]
    return f"({'|'.join(escaped)})" if escaped else ""


def _run_git(
    repository: GitRepository,
    arguments: Sequence[str],
) -> str:
    """以固定超时和明确仓库路径执行一次只读 Git 命令。

    参数:
        - `repository`：已经通过项目目录白名单校验的 Git 仓库。
        - `arguments`：`git` 子命令及参数，类型为 `Sequence[str]`；调用方只能
          传入本模块固定生成的 `log`、`show` 参数。

    Returns:
        标准输出文本。Git 不可用、超时、仓库损坏或返回非零状态时抛出
        `CodeToolError`，由上层记录降级而不影响当前源码诊断。
    """
    if shutil.which("git") is None:
        raise CodeToolError("运行环境未安装 git，无法查询代码历史")
    command = [
        "git",
        "-c",
        f"safe.directory={repository.path}",
        "-C",
        str(repository.path),
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=HISTORY_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodeToolError("Git 历史查询超时") from exc
    if completed.returncode not in (0, 1):
        message = completed.stderr.strip() or "未知 Git 错误"
        raise CodeToolError(f"Git 历史查询失败: {message[:300]}")
    return completed.stdout


def _parse_log_output(repository: GitRepository, output: str) -> List[Dict[str, Any]]:
    """把 `git log --name-only` 的文本输出转换为结构化提交记录。

    参数:
        - `repository`：当前提交所属的子仓库。
        - `output`：使用记录分隔符和字段分隔符生成的 Git 文本输出，类型为
          `str`。

    Returns:
        按输出顺序保存的提交字典。每条记录包含提交哈希、作者时间、提交说明和
        变更文件；路径统一转换为项目内相对路径，便于后续读取源码快照。
    """
    events: List[Dict[str, Any]] = []
    for chunk in output.split("\x1e"):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        metadata = lines[0].split("\x1f")
        if len(metadata) < 4:
            continue
        commit_hash, authored_at, committed_at, subject = metadata[:4]
        files: List[str] = []
        for value in lines[1:]:
            if not value.startswith(("+", "-", "@")):
                relative_file = f"{repository.relative_path}/{value}".strip("/")
                relative_parts = set(Path(relative_file).parts)
                if (
                    Path(relative_file).suffix.casefold() in _SOURCE_SUFFIXES
                    and not relative_parts.intersection(IGNORED_REPOSITORY_PARTS)
                ):
                    files.append(relative_file)
        if not files:
            continue
        # Git 默认按路径输出变更文件；如果直接截断，会优先保留 action 和 config，
        # 把真正承载业务守卫的 Risk/Service 文件挤掉。先按源码可信度排序再限制数量。
        ordered_files = sorted(
            dict.fromkeys(files),
            key=_history_file_priority,
        )[:MAX_HISTORY_FILES_PER_COMMIT]
        events.append(
            {
                "repository": repository.relative_path or ".",
                "commit": commit_hash,
                "short_commit": commit_hash[:12],
                "authored_at": authored_at,
                "committed_at": committed_at,
                "subject": subject,
                "files": ordered_files,
            }
        )
    return events


def _history_file_priority(file_name: str) -> tuple:
    """为历史快照文件计算源码可信度排序键。

    参数:
        - `file_name`：项目内相对文件路径，类型为 `str`。

    Returns:
        供升序排序的稳定键。生产服务、领域实现和配置定义优先于控制器、接口入口、
        文档与测试；同一提交快照预算有限时，先读取更可能承载门槛常量和功能开关的
        文件，避免入口文件中的调用注释占满上下文。
    """
    normalized_file = file_name.replace("\\", "/").casefold()
    lowered = f"/{normalized_file}"
    if any(part in lowered for part in ("/services/", "/service/", "/domain/", "/risk/")):
        score = 0
    elif "/config/" in lowered or "/conf/" in lowered:
        score = 1
    elif "/action/" in lowered or "/controller/" in lowered:
        score = 2
    elif "/api/" in lowered:
        score = 3
    elif lowered.endswith(".md") or "/docs/" in lowered:
        score = 6
    else:
        score = 4
    if "/tests/" in lowered or "/phpunit/" in lowered:
        score += 10
    return score, lowered


def _snapshot_lines(
    repository: GitRepository,
    event: Dict[str, Any],
    terms: Sequence[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """读取提交中的相关源码快照并保留命中行附近上下文。

    参数:
        - `repository`：提交所属的只读 Git 子仓库。
        - `event`：`_parse_log_output` 解析出的单条提交记录。
        - `terms`：触发本次历史查询的业务检索词，类型为 `Sequence[str]`。

    Returns:
        `(快照行, 关联代码词)`。快照行带提交哈希、项目内路径和真实历史行号；
        关联代码词用于回到当前工作区读取类、方法或配置定义，不直接作为答案。
    """
    snapshot: List[Dict[str, Any]] = []
    related: List[str] = []
    commit_hash = str(event.get("commit") or "")
    ordered_files = sorted(
        (str(value) for value in event.get("files") or []),
        key=_history_file_priority,
    )
    for project_file in ordered_files:
        repository_file = str(project_file)
        if repository.relative_path and repository_file.startswith(f"{repository.relative_path}/"):
            repository_file = repository_file[len(repository.relative_path) + 1 :]
        try:
            source = _run_git(repository, ["show", f"{commit_hash}:{repository_file}"])
        except CodeToolError:
            continue
        if len(source.encode("utf-8", errors="ignore")) > MAX_HISTORY_FILE_BYTES:
            continue
        source_lines = source.splitlines()
        folded_terms = [term.casefold() for term in terms]
        matching_numbers = [
            number
            for number, content in enumerate(source_lines, start=1)
            if any(term in content.casefold() for term in folded_terms)
        ]
        if not matching_numbers:
            continue
        # 类常量、功能枚举和框架属性通常位于文件头，行内不一定重复业务词。
        # 对每个相关文件先保留前 40 行，再合并命中窗口，才能看到 MIN_VERSION、
        # 配置开关和入口声明等决定功能是否存在及从何时生效的结构证据。
        included_numbers = set(range(1, min(40, len(source_lines)) + 1))
        for number in matching_numbers[:8]:
            included_numbers.update(range(max(1, number - 16), min(len(source_lines), number + 16) + 1))
        for number in sorted(included_numbers)[:MAX_HISTORY_LINES_PER_COMMIT]:
            content = source_lines[number - 1]
            project_path = f"{repository.relative_path}/{repository_file}".strip("/")
            snapshot.append(
                {
                    "repository": event["repository"],
                    "commit": commit_hash,
                    "short_commit": event["short_commit"],
                    "file": project_path,
                    "line": number,
                    "content": content.strip(),
                    "is_match": any(term in content.casefold() for term in folded_terms),
                }
            )
            for match in _IDENTIFIER_PATTERN.finditer(content):
                value = match.group(0).rstrip("(").strip()
                value = value.split("::", 1)[0] if value.count("::") > 1 else value
                if len(value) >= 3 and value not in related:
                    related.append(value)
        if len(snapshot) >= MAX_HISTORY_LINES_PER_COMMIT:
            break
    return snapshot[:MAX_HISTORY_LINES_PER_COMMIT], related[:16]


def search_repository_history(
    root: str,
    project: str,
    terms: Iterable[str],
    *,
    max_commits: int = MAX_HISTORY_COMMITS,
) -> Dict[str, Any]:
    """在项目内所有独立 Git 仓库中搜索相关提交和源码快照。

    参数:
        - `root`：项目白名单根目录，类型为 `str`。
        - `project`：当前诊断项目标识，类型为 `str`。
        - `terms`：由问题拆出的业务检索词，类型为 `Iterable[str]`；不接受完整
          shell 命令或任意 Git 选项。
        - `max_commits`：全局最多返回的历史提交数，类型为 `int`。

    Returns:
        包含 `events`、`code_terms`、`repositories` 和 `warnings` 的审计结果。
        最早事件由时间排序确定，调用方只能表述为“可访问 Git 历史中的最早相关
        提交”，不能从单一工作区擅自断言线上首发日期。
    """
    if max_commits < 1 or max_commits > 50:
        raise CodeToolError("历史提交数量必须在 1 到 50 之间")
    selected_terms: List[str] = []
    for value in terms:
        term = str(value or "").strip()
        if 2 <= len(term) <= 80 and term not in selected_terms:
            selected_terms.append(term)
    selected_terms = selected_terms[:MAX_HISTORY_TERMS]
    pattern = _pickaxe_pattern(selected_terms)
    if not pattern:
        return {
            "terms": [],
            "repositories": [],
            "events": [],
            "code_terms": [],
            "warnings": ["问题没有可用于查询 Git 历史的业务检索词"],
        }

    repositories = discover_git_repositories(root, project)
    if not repositories:
        return {
            "terms": selected_terms,
            "repositories": [],
            "events": [],
            "code_terms": [],
            "warnings": ["项目目录内未发现可查询的 Git 仓库"],
        }

    events: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for repository in repositories:
        # 先用提交说明检索。Git 对提交说明的索引远快于逐提交计算源码差异，
        # 大多数“何时引入/从哪个版本开始”的维护提交会直接说明业务名称。
        message_arguments = [
            "log",
            "--all",
            "--regexp-ignore-case",
            "--fixed-strings",
            "--no-color",
            "--date=iso-strict",
            "--format=%x1e%H%x1f%aI%x1f%cI%x1f%s",
            "--name-only",
        ]
        for term in selected_terms:
            message_arguments.append(f"--grep={term}")
        message_arguments.append("--")
        try:
            message_output = _run_git(repository, message_arguments)
        except CodeToolError as exc:
            warnings.append(f"{repository.relative_path or '.'}: {exc}")
            continue
        message_events = _parse_log_output(repository, message_output)
        if message_events:
            events.extend(message_events)
            continue

        # 提交说明没有命中时才退化为差异内容检索。该通道能发现“提交说明未写业务词，
        # 但源码改动包含该能力”的情况；固定超时和仓库数量上限仍由调用工具约束。
        try:
            output = _run_git(
                repository,
                [
                    "log",
                    "--all",
                    "--regexp-ignore-case",
                    "--no-color",
                    "--date=iso-strict",
                    "--format=%x1e%H%x1f%aI%x1f%cI%x1f%s",
                    "--name-only",
                    "-G",
                    pattern,
                    "--",
                ],
            )
        except CodeToolError as exc:
            warnings.append(f"{repository.relative_path or '.'}: {exc}")
            continue
        events.extend(_parse_log_output(repository, output))

    def event_time(event: Dict[str, Any]) -> datetime:
        """解析提交作者时间并返回可比较的 UTC 时间。

        参数:
            - `event`：单条 Git 提交记录，`authored_at` 使用 ISO 8601 格式。

        Returns:
            可排序时间；字段损坏时返回最小时间，保证历史查询仍可用于审计。
        """
        value = str(event.get("authored_at") or "")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    events.sort(key=lambda item: (event_time(item), item.get("commit", "")))
    selected_events = events[:max_commits]
    by_repository = {item.relative_path: item for item in repositories}
    code_terms: List[str] = []
    for event in selected_events:
        repository = by_repository.get(str(event.get("repository") or ""))
        if repository is None:
            continue
        snapshot, related = _snapshot_lines(repository, event, selected_terms)
        event["snapshot_lines"] = snapshot
        event["ref"] = f"git:{event['short_commit']}"
        for value in related:
            if value not in code_terms:
                code_terms.append(value)
        for project_file in event.get("files") or []:
            stem = Path(str(project_file)).stem
            if len(stem) >= 6 and stem not in code_terms:
                code_terms.append(stem)
    return {
        "terms": selected_terms,
        "repositories": [item.relative_path or "." for item in repositories],
        "events": selected_events,
        "code_terms": code_terms[:20],
        "warnings": warnings,
    }


__all__ = [
    "GitRepository",
    "discover_git_repositories",
    "history_search_terms",
    "is_history_question",
    "select_history_terms_from_current_hits",
    "search_repository_history",
]
