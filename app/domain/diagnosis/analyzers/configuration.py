"""活动配置源码的通用数值事实解析。

该模块只读取已经进入诊断证据窗口的 PHP 配置行。它先使用问题中的活动标识限定
到真实目录，再解析数组层级、配置键以及数字/布尔字面量；不读取业务数据库，也
不输出密钥、令牌、URL 或其他字符串值。相似命中只用于选择候选，最终事实始终
引用当前源码中的配置行。
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.code_tools import extract_generic_anchors
from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.query_understanding import detect_semantic_concepts


_SCOPE_IDENTIFIER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]{3,})(?![A-Za-z0-9_])")
_GROUP_OPEN_PATTERN = re.compile(
    r"^['\"]([A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*\["
)
_LITERAL_ASSIGNMENT_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*"
    r"(?P<value>-?\d+(?:\.\d+)?|true|false|'[^'\n]{1,80}')(?=\s*[,}\]])",
    re.IGNORECASE,
)
_INLINE_COMMENT_PATTERN = re.compile(r"//\s*(.+?)\s*$")
_COMMENT_PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_SAFE_CODE_CONSTANT_PATTERN = re.compile(r"^\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_REWARD_LABEL_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:美元|金币|钻石|足球券|碎片|天|日|次)$"
)
# VIP/贵族配置按等级重复出现 `N => getByTestMode(测试配置, 正式配置)`。
# 枚举类问题需要读取每个等级的第一段（测试环境）配置，而不是只挑一个数值。
_LEVEL_BLOCK_PATTERN = re.compile(r"(?m)^\s*(\d+)\s*=>\s*getByTestMode\(")
_TEST_BRANCH_MARKER = "//正式配置"
_PRIZE_ITEM_PATTERN = re.compile(
    r"'type'\s*=>\s*(?P<type>[A-Za-z0-9_\\:]+)\s*,\s*"
    r"'gid'\s*=>\s*(?P<gid>\d+)\s*,\s*"
    r"'days'\s*=>\s*(?P<days>\d+)\s*,\s*"
    r"'num'\s*=>\s*(?P<num>\d+)",
    re.S,
)
_PRIZE_TYPE_LABELS = {
    "prize_type_noble_card": "贵族卡",
    "prize_type_prop": "道具",
    "prize_type_noble_exp_card": "贵族体验卡",
}


def _analyze_vip_prize_levels(lines: Sequence[EvidenceLine]) -> List[Dict[str, Any]]:
    """枚举 VIP/贵族等级在测试环境附赠的奖品清单。

    参数:
        - `lines`：已展开的源码证据行，类型为 `Sequence[EvidenceLine]`。调用方
          需要在枚举类问题中提供完整配置文件窗口，否则只能看到部分等级。

    Returns:
        每个等级一条事实，`category` 标明等级，`statement` 列出测试分支的奖品
        类型、数量和有效天数。没有等级块或测试分支为空时返回空列表，由上层
        报告证据缺口，不能用正式环境配置冒充测试环境。
    """
    by_file: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        by_file.setdefault(item.file, []).append(item)
    best_file = ""
    best_text = ""
    best_count = 0
    for file_name, file_lines in by_file.items():
        text = "\n".join(
            item.content for item in sorted(file_lines, key=lambda row: row.line)
        )
        if "getByTestMode(" not in text or "'prize'" not in text:
            continue
        count = len(_LEVEL_BLOCK_PATTERN.findall(text))
        if count > best_count:
            best_file, best_text, best_count = file_name, text, count
    if not best_file:
        return []

    facts: List[Dict[str, Any]] = []
    matches = list(_LEVEL_BLOCK_PATTERN.finditer(best_text))
    for index, match in enumerate(matches):
        level = int(match.group(1))
        block_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(best_text)
        )
        block = best_text[match.end():block_end]
        test_branch = block.split(_TEST_BRANCH_MARKER, 1)[0]
        items = list(_PRIZE_ITEM_PATTERN.finditer(test_branch))
        if not items:
            continue
        parts = []
        for prize in items:
            raw_type = prize.group("type").split("::")[-1].casefold()
            label = _PRIZE_TYPE_LABELS.get(raw_type, prize.group("type"))
            parts.append(
                f"{label} x{int(prize.group('num'))}（gid={int(prize.group('gid'))}，"
                f"{int(prize.group('days'))} 天）"
            )
        statement = f"等级 {level}：" + "；".join(parts)
        line_number = next(
            (
                item.line
                for item in by_file[best_file]
                if re.match(rf"^\s*{level}\s*=>\s*getByTestMode\(", item.content)
            ),
            1,
        )
        facts.append(
            {
                "category": f"VIP{level}级附赠奖品",
                "statement": statement,
                "evidence_refs": [f"{best_file}:{line_number}"],
            }
        )
    return facts

# 只过滤问题中常见的协议、环境和语言缩写。活动标识、类名、配置键不在此处
# 固定，因此未知活动仍可由问题与真实路径的字符匹配进入配置解析。
_GENERIC_SCOPE_IDENTIFIERS = {
    "api", "config", "development", "http", "https", "json", "php", "production",
    "redis", "sql", "test", "testing", "url", "yaml",
}


@dataclass(frozen=True)
class _ConfigLiteralCandidate:
    """描述一个已定位到源码行的配置字面量及其选择依据。"""

    line: EvidenceLine
    group_path: Tuple[str, ...]
    key: str
    value_text: str
    category: str
    score: int
    direct_score: int
    key_score: int
    path_score: int
    context: str
    source_comment: str


@dataclass
class _OpenConfigGroup:
    """保存当前 PHP 配置数组的缩进、路径、注释和已见内容。"""

    indent: int
    key: str
    comments: str
    content_parts: List[str]


def _scope_identifier(question: str, lines: Sequence[EvidenceLine]) -> str:
    """选择问题中能够唯一指向证据目录的活动标识。

    参数:
        - `question`：用户原始业务问题，类型为 `str`；其中的活动目录、类名或配置
          标识用于缩小分析范围。
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。

    Returns:
        与一个或多个证据文件路径匹配、且没有被所有文件共同命中的最具体标识；
        没有可靠目录标识时返回空字符串。过滤共同命中可避免项目名 `waky3` 把全部
        证据误当成同一活动。
    """
    file_paths = sorted({item.file.replace("\\", "/").casefold() for item in lines})
    if not file_paths:
        return ""
    ranked: List[Tuple[int, str]] = []
    for match in _SCOPE_IDENTIFIER_PATTERN.finditer(question):
        token = match.group(1)
        folded = token.casefold()
        if folded in _GENERIC_SCOPE_IDENTIFIERS:
            continue
        matched_files = [path for path in file_paths if folded in path]
        if not matched_files:
            continue
        # 活动标识通常出现在一部分证据路径中。若一个词命中全部文件，它更可能
        # 是项目名或根目录名，不能作为活动级过滤条件。
        if len(file_paths) > 1 and len(matched_files) == len(file_paths):
            continue
        specificity = len(folded) * 3 + len(matched_files)
        if any(char.isdigit() for char in folded):
            specificity += 30
        if any(char.isupper() for char in token[1:]):
            specificity += 18
        ranked.append((specificity, folded))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked else ""


def _scoped_config_lines(lines: Sequence[EvidenceLine], scope: str) -> List[EvidenceLine]:
    """读取活动标识命中的配置 PHP 行，并优先保留正式 `config.php`。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `scope`：`_scope_identifier` 返回的活动路径标识，类型为 `str`。

    Returns:
        同一活动目录下、已排序的 PHP 配置行。存在 `config.php` 时只返回该文件，
        避免语言包、比赛赔率和配置之间的通用数字互相污染；没有 `config.php`
        时保留全部匹配行，由后续键和注释匹配继续筛选。
    """
    scoped = [
        item
        for item in lines
        if not scope or scope in item.file.replace("\\", "/").casefold()
    ]
    config_lines = [
        item
        for item in scoped
        if item.file.replace("\\", "/").casefold().endswith("/config.php")
    ]
    selected = config_lines or scoped
    return sorted(selected, key=lambda item: (item.file, item.line))


def _activity_root(file_name: str) -> str:
    """从证据路径提取活动级目录标识。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。

    Returns:
        `crontab/<活动>`、`logic/<活动>` 或 `action_activity/<活动>` 形式的
        小写目录标识；路径不含活动目录时返回空字符串。`*Control.php` 会去掉
        文件名后缀，使控制器与同活动的配置目录可以归一化比较。
    """
    parts = file_name.replace("\\", "/").split("/")
    markers = {"crontab", "logic", "action_activity"}
    for index, part in enumerate(parts[:-1]):
        if part.casefold() not in markers:
            continue
        activity = parts[index + 1]
        if activity.casefold().endswith("control.php"):
            activity = activity[: -len("Control.php")]
        return f"{part.casefold()}/{activity.casefold()}"
    return ""


def _lines_in_supported_config_dirs(
    lines: Sequence[EvidenceLine],
) -> List[EvidenceLine]:
    """在无法从问题识别活动标识时，只保留有同目录源码佐证的配置文件行。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。

    Returns:
        仅包含 `config.php` 且同目录下存在非配置文件证据的行。中文活动名无法
        直接映射到英文目录时，证据里可能同时出现多个活动的配置；没有同目录
        Boot、Logic 或控制器佐证的配置很可能只是语义召回的噪声，不能据此回答。
    """
    normalized = [
        (item, item.file.replace("\\", "/").casefold()) for item in lines
    ]
    # 中文活动名无法映射目录时，证据里可能同时存在多个活动的 Boot 和配置。
    # 只要活动目录不唯一，就不能用其中任意一个配置回答，否则会把“游戏榜”
    # 答成“超级士兵”；此时返回空列表，让上层明确报告证据缺口。
    roots = {_activity_root(file_name) for _, file_name in normalized}
    if len({root for root in roots if root}) > 1:
        return []
    source_dirs = {
        file_name.rsplit("/", 1)[0]
        for _, file_name in normalized
        if not file_name.endswith("/config.php")
    }
    kept = [
        item
        for item, file_name in normalized
        if file_name.endswith("/config.php")
        and file_name.rsplit("/", 1)[0] in source_dirs
    ]
    return kept


def _safe_literal_text(raw_value: str) -> str:
    """校验字符串字面量是否适合写入诊断事实。

    参数:
        - `raw_value`：正则从 PHP 配置行捕获的原始值，类型为 `str`；字符串值带有
          单引号。

    Returns:
        数字和布尔值原样返回；字符串仅保留代码常量引用或“多少美元/金币”这类
        面向用户的奖励标签。URL、路径、令牌、密钥和普通文本一律返回空字符串，
        避免配置解析器扩大敏感信息暴露面。
    """
    if not raw_value.startswith("'"):
        return raw_value
    inner = raw_value[1:-1].strip()
    if _SAFE_CODE_CONSTANT_PATTERN.fullmatch(inner) or _SAFE_REWARD_LABEL_PATTERN.fullmatch(inner):
        return inner
    return ""


def _question_anchor_score(context: str, anchors: Sequence[str]) -> int:
    """计算源码上下文与问题业务片段的重合权重。

    参数:
        - `context`：配置行、所在数组标题和邻近中文注释的组合文本，类型为 `str`。
        - `anchors`：`extract_generic_anchors` 从问题生成的中文业务片段，类型为
          `Sequence[str]`。

    Returns:
        可直接用于候选排序的非负分值。四字以上完整片段权重大于两字核心词，
        因此“基础奖池”会排在只共享“活动”等泛化词的行之前。
    """
    folded = context.casefold()
    score = 0
    for anchor in anchors:
        value = anchor.casefold()
        if len(value) >= 2 and value in folded:
            score += len(value) * (4 if len(value) >= 4 else 1)
    return score


def _inline_comment(content: str) -> str:
    """提取配置行末尾中文注释，供配置事实与问题语义对齐。

    参数:
        - `content`：单行 PHP 配置源码，类型为 `str`。

    Returns:
        `//` 后的注释文本；没有行尾注释时返回空字符串。仅用于解释配置，不会
        把注释中的数值直接当成事实。
    """
    match = _INLINE_COMMENT_PATTERN.search(content)
    return match.group(1).strip() if match else ""


def _literal_candidates(lines: Sequence[EvidenceLine], question: str) -> List[_ConfigLiteralCandidate]:
    """从活动配置数组中提取数字、布尔字面量及真实数组路径。

    参数:
        - `lines`：已限定到活动目录的配置源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始问题，类型为 `str`；仅用于对配置键、注释和业务概念
          排序，不把问题文本写入结论。

    Returns:
        按相关度从高到低排列的配置字面量候选。字符串、密钥、URL、函数调用和
        表达式值不会进入候选，防止诊断报告输出敏感值或把运行态表达式误当常量。
    """
    anchors = extract_generic_anchors(question, max_anchors=64)
    question_concepts = {item.name for item in detect_semantic_concepts(question)}
    grouped: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        grouped.setdefault(item.file, []).append(item)

    candidates: List[_ConfigLiteralCandidate] = []
    for file_lines in grouped.values():
        stack: List[_OpenConfigGroup] = []
        recent_comments: List[str] = []
        for item in file_lines:
            stripped = item.content.strip()
            if not stripped:
                continue
            indent = len(item.content) - len(item.content.lstrip())
            if stripped.startswith("//"):
                recent_comments.append(stripped.lstrip("/").strip())
                recent_comments = recent_comments[-4:]
                continue

            while stack and indent <= stack[-1].indent:
                stack.pop()
            group_match = _GROUP_OPEN_PATTERN.match(item.content.lstrip())
            if group_match:
                group_comments = " ".join(recent_comments)
                inline = _inline_comment(item.content)
                stack.append(
                    _OpenConfigGroup(
                        indent=indent,
                        key=group_match.group(1),
                        comments=f"{group_comments} {inline}".strip(),
                        content_parts=[],
                    )
                )
                recent_comments = []

            # 组成员、事件名和子数组都可能位于当前字面量之前若干行。把这些
            # 已见内容挂到所有打开的分组上，使 `sendGiftLevels` 等真实业务标识
            # 能帮助同组配置排序，同时不读取证据窗口以外的源码。
            for group in stack:
                group.content_parts.append(stripped)
                group.content_parts = group.content_parts[-12:]
            group_path = tuple(entry.key for entry in stack)
            group_context = " ".join(
                value
                for entry in stack
                for value in (" ".join(entry.content_parts), entry.comments)
                if value
            )
            surrounding_comments = " ".join(recent_comments)
            for literal in _LITERAL_ASSIGNMENT_PATTERN.finditer(item.content):
                key = literal.group("key")
                value_text = _safe_literal_text(literal.group("value"))
                if not value_text:
                    continue
                inline = _inline_comment(item.content)
                context = " ".join(
                    value
                    for value in (
                        item.content,
                        inline,
                        surrounding_comments,
                        group_context,
                        " ".join(group_path),
                    )
                    if value
                )
                direct_score = _question_anchor_score(" ".join((inline, key)), anchors)
                total_score = _question_anchor_score(context, anchors)
                context_concepts = {
                    concept.name for concept in detect_semantic_concepts(context)
                }
                total_score += len(question_concepts.intersection(context_concepts)) * 8
                key_concepts = {
                    concept.name for concept in detect_semantic_concepts(key)
                }
                path_concepts = {
                    concept.name
                    for concept in detect_semantic_concepts(" ".join(group_path))
                }
                key_score = len(question_concepts.intersection(key_concepts)) * 12
                path_score = len(question_concepts.intersection(path_concepts)) * 10
                total_score += key_score + path_score
                # 用户直接写出配置键时优先精确匹配路径末级键，而不是依赖中文
                # 注释；该规则同样适用于未预置的新配置。
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", question):
                    total_score += 40
                if total_score <= 0:
                    continue
                category = "配置开关" if value_text.casefold() in {"true", "false"} else "配置数值"
                candidates.append(
                    _ConfigLiteralCandidate(
                        line=item,
                        group_path=group_path,
                        key=key,
                        value_text=value_text,
                        category=category,
                        score=total_score,
                        direct_score=direct_score,
                        key_score=key_score,
                        path_score=path_score,
                        context=context,
                        source_comment=inline,
                    )
                )
            recent_comments = []
    candidates.sort(key=lambda item: (-item.score, item.line.file, item.line.line))
    return candidates


def _question_ordinal(question: str) -> Optional[int]:
    """提取“第一档、第 2 名”等问题中的一基序号。

    参数:
        - `question`：用户原始问题，类型为 `str`。

    Returns:
        一到十之间的序号；没有明确序号时返回 `None`。该值只用于在同名重复配置
        中选择正确条目，不参与业务结论推断。
    """
    match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*(?:档|名|级|个|项)", question)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        value = int(raw)
    else:
        value = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(raw)
    return value if value and 1 <= value <= 10 else None


def _numeric_value(value_text: str) -> Optional[float]:
    """把配置字面量转换成数值，非数值返回空。

    参数:
        - `value_text`：配置中的数字、布尔或奖励标签，类型为 `str`。

    Returns:
        可比较的浮点值；布尔和用户奖励标签不参与“最大值”选择时返回 `None`。
    """
    try:
        return float(value_text)
    except (TypeError, ValueError):
        return None


def _select_candidates(candidates: Sequence[_ConfigLiteralCandidate], question: str) -> List[_ConfigLiteralCandidate]:
    """选择回答问题所需的最具体配置组，避免把同文件其他数组值混入结论。

    参数:
        - `candidates`：`_literal_candidates` 生成的相关配置字面量，类型为
          `Sequence[_ConfigLiteralCandidate]`。
        - `question`：用户原始问题，类型为 `str`；用于识别“最大、提前、第一档”等
          通用筛选动作。

    Returns:
        按源码行排序的候选。优先使用最相关候选所在的完整数组组；问题明确询问
        提前时间或最大值时只保留最相关的单项。列表中不存在候选时返回空列表。
    """
    if not candidates:
        return []
    mentioned_keys = {
        value.rsplit(".", 1)[-1].casefold()
        for value in re.findall(r"`([^`]+)`", question)
        if value
    }
    if mentioned_keys:
        matched = [
            item
            for item in candidates
            if item.key.casefold() in mentioned_keys
            or ".".join((*item.group_path, item.key)).casefold() in mentioned_keys
        ]
        if matched:
            best_match = matched[0]
            same_key_group = [
                item
                for item in matched
                if item.line.file == best_match.line.file
                and item.group_path == best_match.group_path
                and item.key == best_match.key
            ]
            ordinal = _question_ordinal(question)
            if ordinal is not None and same_key_group:
                ordered = sorted(same_key_group, key=lambda item: item.line.line)
                return [ordered[min(ordinal - 1, len(ordered) - 1)]]
            if "最大" in question:
                numeric = [item for item in same_key_group if _numeric_value(item.value_text) is not None]
                if numeric:
                    return [max(numeric, key=lambda item: _numeric_value(item.value_text) or 0.0)]
            if len(mentioned_keys) == 1 and len(same_key_group) == 1:
                return same_key_group
            deduplicated: Dict[Tuple[Tuple[str, ...], str], _ConfigLiteralCandidate] = {}
            for item in matched:
                deduplicated.setdefault((item.group_path, item.key), item)
            return sorted(deduplicated.values(), key=lambda item: (item.line.file, item.line.line))
    best = candidates[0]
    targeted = any(value in question for value in ("提前", "截止")) or "最大" in question
    if targeted:
        return [best]
    same_group = [
        item
        for item in candidates
        if item.line.file == best.line.file and item.group_path == best.group_path
    ]
    # 同一数组组通常共同描述一个业务设置。用户询问一组参数时，只保留最高行
    # 会漏掉“最少投注、步长、是否允许”等并列项，因此默认返回整个最相关组；
    # 提前时间、截止时间和最大值等单点问题仍在上方走单项路径。
    key_score = max((item.key_score for item in same_group), default=0)
    if key_score > 0:
        selected = [item for item in same_group if item.key_score == key_score]
    else:
        path_score = max((item.path_score for item in same_group), default=0)
        selected = [item for item in same_group if item.path_score == path_score] if path_score > 0 else same_group
    # “有没有保底和价格限制”这类存在性提问通常对应同一父级下的多个 enabled
    # 开关。只返回得分最高的一个会漏掉另一个开关，给出“只关闭了价格限制”的
    # 片面结论；这里补全同一父级、同文件内的全部 enabled 开关。
    if (
        selected
        and any(item.key.casefold() == "enabled" for item in selected)
        and any(token in question for token in ("有没有", "是否", "有无"))
    ):
        parent_path = selected[0].group_path[:-1]
        siblings: Dict[Tuple[Tuple[str, ...], str], _ConfigLiteralCandidate] = {}
        for item in candidates:
            if (
                item.key.casefold() == "enabled"
                and item.group_path[:-1] == parent_path
                and item.line.file == selected[0].line.file
            ):
                siblings.setdefault((item.group_path, item.key), item)
        if siblings:
            return sorted(
                siblings.values(), key=lambda item: (item.line.file, item.line.line)
            )
    return sorted(selected, key=lambda item: (item.line.file, item.line.line))


def _configuration_literal_facts(lines: Sequence[EvidenceLine], question: str) -> List[Dict[str, Any]]:
    """返回目录范围内的配置数值与开关事实。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        最多十二条带真实源码引用的配置事实；没有可唯一确定的活动目录或配置
        字面量时返回空列表，让调用方继续使用其他分析器或明确报告证据缺口。
    """
    scope = _scope_identifier(question, lines)
    # 中文活动名（例如“游戏榜”）无法直接变成英文目录名。此时若证据里混入
    # 多个活动的配置文件，按全仓候选排序很容易答到别的活动。没有活动标识时
    # 只接受同目录存在非配置文件证据的配置，避免用语义召回的噪声配置作答。
    scoped_lines = _lines_in_supported_config_dirs(lines) if not scope else lines
    config_lines = _scoped_config_lines(scoped_lines, scope)
    candidates = _literal_candidates(config_lines, question)
    selected = _select_candidates(candidates, question)
    facts: List[Dict[str, Any]] = []
    for item in selected[:12]:
        key_path = ".".join((*item.group_path, item.key))
        facts.append(
            {
                "category": item.category,
                "statement": f"配置 `{key_path}` 的源码字面值为 `{item.value_text}`。",
                "evidence_refs": [item.line.ref],
                "source_comment": item.source_comment,
                "source_value": item.value_text,
                "source_key": key_path,
            }
        )
    return facts


def _configuration_fact_conclusion(facts: Sequence[Dict[str, Any]]) -> str:
    """把配置事实压缩为直接回答，并提示注释与代码值的冲突。

    参数:
        - `facts`：`_configuration_literal_facts` 生成的配置事实，类型为
          `Sequence[Dict[str, Any]]`。

    Returns:
        以真实配置键和代码字面值组成的结论。若同一配置行的注释百分比与数值
        不一致，会明确以代码为准；不会把注释中的说明当成可执行配置值。
    """
    values = []
    for fact in facts:
        statement = str(fact.get("statement") or "").strip()
        match = re.search(r"配置 `([^`]+)` 的源码字面值为 `([^`]+)`", statement)
        if not match:
            continue
        key_path, value_text = match.groups()
        rendered = "未开启" if value_text.casefold() == "false" else "开启" if value_text.casefold() == "true" else value_text
        values.append(f"`{key_path}={rendered}`")
    if not values:
        return ""
    conflicts = []
    for fact in facts:
        key_path = str(fact.get("source_key") or "")
        value_text = str(fact.get("source_value") or "")
        comment = str(fact.get("source_comment") or "")
        percent_match = _COMMENT_PERCENT_PATTERN.search(comment)
        try:
            numeric_value = float(value_text)
        except ValueError:
            continue
        if percent_match and 0 < numeric_value <= 1:
            comment_percent = float(percent_match.group(1))
            if abs(numeric_value * 100 - comment_percent) > 0.001:
                conflicts.append(
                    f"`{key_path}` 的源码注释写 {comment_percent:g}%，"
                    f"但代码字面值为 {value_text}，两者不一致"
                )
    suffix = "".join(f"；{item}" for item in conflicts)
    return (
        "已从活动配置源码确认："
        + "；".join(values)
        + "。以上结论以代码字面值为准"
        + suffix
        + "。"
    )


__all__ = [
    "_configuration_fact_conclusion",
    "_configuration_literal_facts",
]
