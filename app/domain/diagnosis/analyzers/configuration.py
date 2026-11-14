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
    r"(?P<value>-?\d+(?:\.\d+)?|true|false|'[^'\n]{1,80}'|\"[^\"\n]{1,80}\""
    # 配置也常用枚举常量表达类型（`\Conf\FlowType::ACTIVITY_GIFT_WEEK_STAR`）。
    # 常量名不是密钥，可以原样输出；`_safe_literal_text` 会再次校验格式。
    r"|\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*)"
    r"(?=\s*(?:[,}\]]|//|$))",
    re.IGNORECASE,
)
_INLINE_COMMENT_PATTERN = re.compile(r"//\s*(.+?)\s*$")
_COMMENT_PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# 单行赋值键：用于把表达式日期与 `'stime' => ...` 这样的键配对。
_ASSIGNMENT_KEY_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>"
)
# PHP 活动起止时间几乎都写成 `$_dateTime->modify('2026-09-23 12:00:00')` 或
# `getTimestamp('2026-05-09 12:00:00', ...)`。这里只接受完整日期字面量，
# 以便把表达式时间转成可核对的配置事实。
_DATE_LITERAL_PATTERN = re.compile(
    r"['\"](\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)['\"]"
)
# 时间类提问词：出现时提高日期表达式候选的排序，避免“起止时间”被其他数字淹没。
_TIME_QUESTION_WORDS = ("时间", "日期", "起止", "开始", "结束", "开放", "截止")
# 匿名标量数组：`top_fixed => [[1, 5, 100]]`、`prizes => [110,120]` 这类
# 列表元素没有键名，业务含义由位置表示。只接受纯数字，避免把嵌套数组
# 或字符串列表误当成配置值。
_SCALAR_LIST_BODY = r"\s*-?\d+(?:\.\d+)?\s*(?:,\s*-?\d+(?:\.\d+)?\s*)*"
_SCALAR_LIST_PATTERN = re.compile(
    rf"^\s*\[\s*({_SCALAR_LIST_BODY})\s*\](?=\s*(?:,|;|//|$))"
)
# 带数字键的标量数组：`surprise => [1 => [10, 30], 2 => [20, 50]]`。
_KEYED_SCALAR_LIST_PATTERN = re.compile(
    rf"^\s*(?P<key>\d+)\s*=>\s*\[\s*({_SCALAR_LIST_BODY})\s*\]"
    r"(?=\s*(?:,|;|//|$))"
)
# 中文业务意图到常见英文字段名的通用别名。活动配置字段在不同项目中命名
# 不一致，但“比例/库存/门槛/碎片”等业务含义稳定；命中别名时提高该键优先级，
# 避免同文件里的名称、图片地址等文本字段压过真正的数值配置。
_KEY_INTENT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "比例": ("rate", "scale", "ratio", "percent"),
    "费率": ("rate", "fee", "scale"),
    "倍率": ("rate", "scale", "multiple"),
    "库存": ("stock", "inventory"),
    "门槛": ("limit", "threshold", "target", "need"),
    "上限": ("max", "limit", "cap"),
    "截止": ("deadline", "pre_time", "pre_deadline"),
    "价格": ("price", "cost"),
    "名次": ("rank", "top_rank", "top_n"),
    "碎片": ("fragment", "fragments"),
    "概率": ("pro", "probability", "rate"),
    "燃料": ("value", "need", "fuel_value"),
    "金额": ("amount", "money", "dollar"),
    "目标": ("target", "goal"),
    "保底": ("guarantee", "minimum_guarantee"),
    "惊喜": ("surprise",),
    "固定": ("fixed", "top_fixed"),
    "区间": ("range", "surprise"),
    "奖励": ("prize", "prizes", "reward"),
    "奖品": ("prize", "prizes", "reward"),
    "瓜分": ("top_rank", "top_n", "rank"),
    "排名": ("top_rank", "top_n", "rank", "top_prizes"),
    "日榜": ("top_prizes", "day_rank", "rank"),
    "名次": ("top_rank", "top_n", "rank"),
    "金币": ("coin", "gold", "init_coin"),
    "公会": ("guild",),
    "宝箱": ("box", "treasure", "chest"),
    "兜底": ("isfallback", "fallback", "guarantee"),
    # 活动配置常把“指定礼物”写成 `gifts` 数组；问题问礼物数量或 ID 时必须
    # 优先该数组，而不是同文件里的奖池分档。
    "礼物": ("gift", "gifts"),
    # “监听哪些游戏类型/发奖类型”对应游戏榜的 `allow_types`；类型类配置在
    # 不同活动里也叫 `type`、`types` 或 `kind`，统一按业务意图匹配。
    "类型": ("type", "types", "allow_types", "kind"),
    "监听": ("allow", "listen", "allow_types"),
    "发奖": ("allow_types", "prize_type", "type"),
    # 等级/阶段/遗迹类问题必须优先匿名或数字键的 `levels` 组，不能被同文件
    # 的日榜、瓜分表等排名数组抢占。
    "等级": ("levels", "level", "grade", "tier"),
    "档位": ("levels", "level", "tier"),
    "阶段": ("levels", "stage", "stages"),
    "遗迹": ("levels", "relics", "ruins"),
    # “哪些阶段支持跳转 X”对应布尔开关 `is_can_jump_x`；不同活动也可能写成
    # `jump`、`can_jump`，只用于候选排序，不据此生成结论。
    "跳转": ("is_can_jump_x", "jump", "jump_x", "can_jump"),
}
_SAFE_CODE_CONSTANT_PATTERN = re.compile(r"^\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_REWARD_LABEL_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:美元|金币|钻石|足球券|碎片|天|日|次)$"
)
# 面向用户的奖励/关卡名称：英文名（Bubble*7 days、Shining dusk*7 days）和
# 中文名（佩特拉古城、长城）在活动配置里大量出现。它们不是密钥或地址，
# 但必须排除 URL、文件路径、翻译键和内网地址，避免扩大敏感信息暴露面。
_SAFE_ENGLISH_LABEL_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9 *()\-.,+]{0,58}$"
)
_UNSAFE_LABEL_FRAGMENTS = (
    "://",
    "http",
    "www.",
    "lang_map",
    "lang.",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    "@",
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
    # 只有目录段才代表活动归属。直接把英文词在整条路径上做子串匹配，会把
    # `Shell Beach` 里的 `shell` 误判成 `ActivityBase/tool/ShellTool.php` 的
    # 活动目录，随后丢掉真正的活动配置。这里先按路径目录段归一化再比较。
    directory_segments: Dict[str, List[str]] = {}
    for path in file_paths:
        for part in path.split("/")[:-1]:
            segment = re.sub(r"[^a-z0-9]", "", part.casefold())
            if len(segment) < 4:
                continue
            directory_segments.setdefault(segment, []).append(path)
    ranked: List[Tuple[int, str]] = []
    for match in _SCOPE_IDENTIFIER_PATTERN.finditer(question):
        token = match.group(1)
        folded = re.sub(r"[^a-z0-9]", "", token.casefold())
        if folded in _GENERIC_SCOPE_IDENTIFIERS or len(folded) < 4:
            continue
        matched_directories = [
            segment
            for segment in directory_segments
            if segment == folded
            or (len(folded) >= 5 and segment.startswith(folded))
            or (len(segment) >= 5 and folded.startswith(segment))
        ]
        if not matched_directories:
            continue
        matched_files = sorted(
            {
                path
                for segment in matched_directories
                for path in directory_segments[segment]
            }
        )
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
    # 中文版本词无法通过英文标识匹配，但“新版”稳定对应目录后缀 `new`。
    # 只有目录段以词根结尾时才参与，避免把 `news`、`renew` 误当成活动版本。
    for phrase, hint in _CHINESE_DIRECTORY_HINTS.items():
        if phrase not in question:
            continue
        for segment, paths in directory_segments.items():
            if not segment.endswith(hint):
                continue
            if len(file_paths) > 1 and len(set(paths)) == len(file_paths):
                continue
            ranked.append((len(segment) * 3 + len(set(paths)) + 12, segment))
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


# 中文营销名与英文目录的通用词根提示：只处理“新版/旧版”这类跨活动稳定存在的
# 版本语义，不保存任何具体活动名到目录的映射。目录段以词根结尾时才参与打分，
# 避免把 `news`、`renew` 等无关目录误认成新版活动。
_CHINESE_DIRECTORY_HINTS: Dict[str, str] = {
    "新版": "new",
    "旧版": "old",
}
# 面向用户的中文展示词：活动标题、pushmsg 文案、关卡名称和同行注释。只保留
# 连续中文片段，URL、路径、翻译键和密钥天然不含中文，不会被抽取。
_CHINESE_PHRASE_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,40}")


def _config_file_root(file_name: str) -> str:
    """为没有活动目录的传统配置文件生成稳定分组标识。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。

    Returns:
        活动目录标识（`crontab/<活动>` 等）或 `config/<文件名>`。传统布局
        （`php/config/noble.php`、`php/conf/pay.php`）没有活动目录，如果直接
        跳过，会把 `noble.php` 这种真正的全局配置排除在活动选择之外，只能
        被同问题里的其他活动配置抢答。
    """
    root = _activity_root(file_name)
    if root:
        return root.split("/")[-1]
    normalized = file_name.replace("\\", "/")
    if normalized.endswith(".php") and (
        "/config/" in normalized or "/conf/" in normalized
    ):
        stem = normalized.rsplit("/", 1)[-1][:-4]
        return f"config/{stem}"
    return ""


def _is_config_definition_file(file_name: str) -> bool:
    """判断证据文件是否为配置定义文件。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。

    Returns:
        活动目录下的 `config.php`，或传统布局 `php/config/*.php`、`php/conf/*.php`
        都算配置定义文件。传统全局配置（`noble.php`）没有活动目录，如果只认
        `config.php`，它会被当成“没有配置佐证”而排除，真正的 VIP 门槛就答不出来。
    """
    folded = file_name.replace("\\", "/").casefold()
    if folded.endswith("/config.php"):
        return True
    return (
        (("/config/" in folded) or ("/conf/" in folded))
        and folded.endswith(".php")
    )


def _activity_display_terms(
    lines: Sequence[EvidenceLine],
) -> Dict[str, Tuple[str, ...]]:
    """从证据行中抽取每个活动目录的中文展示词。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`；
          需要包含活动 `config.php` 的完整窗口，才能覆盖 pushmsg、关卡名和注释。

    Returns:
        活动根（`crontab/<活动>`、`logic/<活动>` 等，小写）到中文片段元组的映射。
        片段来自标题、推送文案、`name.zh` 和注释，长度 2 至 40 个汉字；单个活动
        最多保留 240 个片段，避免异常配置放大评分成本。该结果只用于活动目录
        选择，不会写入诊断结论。
    """
    by_root: Dict[str, set] = {}
    for item in lines:
        root = _activity_root(item.file.replace("\\", "/"))
        if not root:
            continue
        terms = by_root.setdefault(root, set())
        if len(terms) >= 240:
            continue
        for match in _CHINESE_PHRASE_PATTERN.finditer(item.content or ""):
            value = match.group(0).strip()
            if 2 <= len(value) <= 40:
                terms.add(value)
    return {root: tuple(sorted(terms)) for root, terms in by_root.items()}


def _activity_alias_score(question: str, root_lines: Sequence[EvidenceLine]) -> int:
    """计算问题中文片段与活动展示词、目录名的重合度。

    参数:
        - `question`：用户原始业务问题，类型为 `str`；其中的营销名、版本词和
          业务动作用于判断活动归属。
        - `root_lines`：同一活动根下的证据行，类型为 `Sequence[EvidenceLine]`；
          应尽量包含完整配置窗口，否则展示词可能缺失。

    Returns:
        非负分值。问题中文双向片段（2 至 6 字）命中展示词时按长度加权，四字
        以上片段权重更高；目录名命中“新版/旧版”词根时额外加分。该分值只参与
        活动目录排序，不参与结论生成，也不构成活动名硬编码映射。
    """
    display_terms = _activity_display_terms(root_lines)
    # 同一活动可能同时以 crontab/logic/action_activity 出现，合并所有根的展示词。
    terms = sorted({term for values in display_terms.values() for term in values})
    # 上下文只由活动自身的展示词、目录名和源码内容组成，不能把问题本身拼进来，
    # 否则任何问题片段都会“命中”并让所有活动得到相同分值。传统全局配置
    # （`php/config/noble.php`）没有活动目录，展示词为空，但源码内容仍可用于
    # 匹配英文业务词和中文注释。
    context = " ".join(
        [*terms, *display_terms.keys(), *[item.content or "" for item in root_lines]]
    ).casefold()
    if not context:
        return 0
    anchors = [
        value
        for value in extract_generic_anchors(question, max_anchors=32)
        if 2 <= len(value) <= 8
    ]
    # 补充 2 至 6 字连续片段，覆盖“游戏榜”“公会寻宝”这类四字营销名，也覆盖
    # 检索锚点切分后遗漏的短业务词。
    grams = set()
    for anchor in anchors:
        if len(anchor) < 2:
            continue
        for start in range(len(anchor)):
            for size in range(2, min(6, len(anchor) - start) + 1):
                grams.add(anchor[start:start + size])
    score = 0
    matched: List[str] = []
    for value in sorted(grams, key=len, reverse=True):
        if value in matched:
            continue
        # 只在展示词/目录上下文中匹配，避免用问题自身回环得分。
        if value not in context:
            continue
        if any(value in existing for existing in matched):
            continue
        matched.append(value)
        score += len(value) * (4 if len(value) >= 4 else 1)
    # 问题直接写出英文业务词（`VIP`、`year`）时，也参与活动内容匹配：
    # 全局配置 `noble.php` 的注释和图片字段含 `noble_vip`，可据此区分
    # “VIP 升级”与同时检索到的 Ramadan 等活动配置。
    for identifier in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question or ""):
        folded = identifier.casefold()
        if folded in _GENERIC_SCOPE_IDENTIFIERS:
            continue
        if folded in context:
            score += min(len(folded), 8) * 2
    for phrase, hint in _CHINESE_DIRECTORY_HINTS.items():
        if phrase not in question:
            continue
        root_names = " ".join(display_terms.keys())
        if hint and re.search(rf"/[^/]+{hint}$", root_names):
            score += 12
    return score


_CLASS_SUFFIX_PATTERN = re.compile(
    r"(?:service|controller|control|model|request|command|helper|logic|"
    r"manager|handler|boot|export)$",
    re.IGNORECASE,
)


def _term_alias_variants(term: str) -> List[str]:
    """把真实类名转换成可匹配活动目录的字母骨架。

    参数:
        - `term`：检索扩展并经过索引校验的真实代码词，类型为 `str`，例如
          `GameRankPrizeService`。

    Returns:
        去掉 `Service/Controller/Model/Prize...` 等常见后缀后的归一化骨架列表。
        只保留至少六个字母的变体，避免 `Game` 这类短词误命中多个活动。
    """
    raw = re.sub(r"[^A-Za-z0-9]", "", term or "")
    variants: List[str] = []
    current = raw
    while current:
        folded = current.casefold()
        if len(folded) >= 6 and folded not in variants:
            variants.append(folded)
        stripped = _CLASS_SUFFIX_PATTERN.sub("", current)
        if stripped == current or not stripped:
            break
        current = stripped
    return variants


def _compact_subsequence(alias: str, component: str) -> bool:
    """判断目录名是否包含术语的连续字母骨架。

    参数:
        - `alias`：术语归一化后的字母序列，类型为 `str`。
        - `component`：活动目录归一化后的字母序列，类型为 `str`。

    Returns:
        目录按顺序包含术语全部字母、且长度差不超过五个字符时返回 `True`。
        用于 `gamrank` 匹配 `gameRanking` 这类只差元音/词尾的类名与目录名。
    """
    if len(alias) < 6 or len(component) <= len(alias):
        return False
    index = 0
    for char in component:
        if index < len(alias) and char == alias[index]:
            index += 1
    return index == len(alias) and len(component) - len(alias) <= 5


def _activity_structure_score(question: str, root_lines: Sequence[EvidenceLine]) -> int:
    """按问题意图给活动配置的结构匹配打分。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。
        - `root_lines`：同一活动根下的完整配置证据，类型为 `Sequence[EvidenceLine]`。

    Returns:
        非负结构分值。名次问题优先含 `rank_prizes/top_prizes` 的活动；等级/
        阶段问题优先同时含 `levels` 与 `limit` 的活动；类型、跳转问题优先含
        `allow_types`、`is_can_jump_x` 的活动。该分值只用于活动目录排序，
        不生成事实。
    """
    content = " ".join(item.content.casefold() for item in root_lines)
    if not content:
        return 0
    score = 0
    rank_intent = bool(
        re.search(
            r"前\s*\d+\s*名|前[一二三四五六七八九十]+名|第\s*[一二三四五六七八九十\d]+\s*名",
            question,
        )
        or any(token in question for token in ("名次", "排名", "日榜", "周榜", "月榜"))
    )
    # VIP 问题即使没写“等级”二字（“VIP 3 的门槛”）也属于逐级门槛查询，
    # 需要给带 `level` + `score` 的贵族配置加分。
    level_intent = any(
        token in question.casefold()
        for token in ("等级", "档位", "档", "级别", "阶段", "vip")
    )
    if rank_intent and ("'rank_prizes'" in content or "'top_prizes'" in content):
        score += 35
    # 必须确认键后面真的跟了数组开放符：奖品表里的 `'level' => 0` 只是普通
    # 字段，不能被当成等级分组，否则任意大活动都会靠这一个键拿到等级分。
    level_group = bool(re.search(r"['\"]level['\"]\s*=>\s*\[", content))
    levels_group = bool(re.search(r"['\"]levels['\"]\s*=>\s*\[", content))
    if level_intent and levels_group and "'limit'" in content:
        score += 30
    # 全局贵族/等级配置使用 `level.<等级>.score/value` 表达门槛；它与活动
    # 配置的 `levels.limit` 结构不同，但同样是“每个等级门槛”问题的高置信结构。
    if level_intent and level_group and (
        "'score'" in content or "'value'" in content
    ):
        score += 30
    if any(token in question for token in ("类型", "监听", "发奖")) and (
        "'allow_types'" in content
    ):
        score += 35
    if any(token in question for token in ("跳转", "支持")) and (
        "'is_can_jump_x'" in content
    ):
        score += 35
    # 惊喜档/保底档区间问题对应 `surprise` 数组；同文件其他阶段奖励表
    # 虽然也含数字区间，但不是用户问的档位分布。
    if any(token in question for token in ("惊喜", "区间")) and (
        "'surprise'" in content
    ):
        score += 30
    # 不按整个配置文件的 `limit`/数字键出现次数判断数量：大型活动文件里
    # 同名键会分散在多个数组，容易让错误活动恰好凑出目标数量。精确的条目
    # 数量匹配留给 `config_graph` 的候选解析，那里能按数组分组计数。
    return score


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


def _dominant_activity_lines(
    lines: Sequence[EvidenceLine],
    question: str,
) -> Optional[List[EvidenceLine]]:
    """在多个活动目录同时出现时选择与问题最相关的一个。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`；其中的中文业务锚点和
          业务概念用于给活动目录打分。

    Returns:
        主活动目录的证据行；没有任何活动目录或所有目录相关度都为零时返回
        `None`。选择只依赖可复用的业务词、配置文件和证据密度，不保存活动
        目录名或中文名映射，因此新活动同样适用。
    """
    by_root: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        root = _activity_root(item.file.replace("\\", "/"))
        if root:
            by_root.setdefault(root, []).append(item)
    if not by_root:
        return None
    if len(by_root) == 1:
        return lines
    anchors = [
        value
        for value in extract_generic_anchors(question, max_anchors=32)
        if len(value) >= 2
    ]
    concepts = {item.name for item in detect_semantic_concepts(question)}
    scored = []
    for root, root_lines in by_root.items():
        context = " ".join([root, *[item.content for item in root_lines]]).casefold()
        score = 0
        for anchor in anchors:
            value = anchor.casefold()
            if value and value in context:
                score += len(value) * (3 if len(value) >= 4 else 1)
        # 中文营销名在配置里往往只出现在 pushmsg/关卡名/注释中，英文目录名与
        # 问题没有直接重合。展示词评分把这些真实文案带回选择依据，避免行数更多
        # 的错误活动抢走配置。
        score += _activity_alias_score(question, root_lines)
        score += _activity_structure_score(question, root_lines)
        context_concepts = {
            concept.name for concept in detect_semantic_concepts(context)
        }
        score += len(concepts.intersection(context_concepts)) * 6
        has_config = any(
            item.file.replace("\\", "/").casefold().endswith("/config.php")
            for item in root_lines
        )
        has_other = any(
            not item.file.replace("\\", "/").casefold().endswith("/config.php")
            for item in root_lines
        )
        if has_config:
            score += 10
        # 只有配置文件、没有任何入口/服务代码佐证的活动不能靠配置命中就胜出：
        # 检索常常把别的活动的配置文件带进证据，必须要求同目录还有非配置证据。
        qualified = has_config and has_other
        scored.append((score, len(root_lines), root, root_lines, qualified))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    qualified_candidates = [item for item in scored if item[4]]
    if not qualified_candidates or qualified_candidates[0][0] <= 0:
        return None
    return qualified_candidates[0][3]


def scoped_config_evidence(
    lines: Sequence[EvidenceLine],
    question: str,
    scope_terms: Sequence[str] = (),
    include_standalone_configs: bool = False,
) -> Optional[List[EvidenceLine]]:
    """按活动范围筛选可用于配置回答的证据行。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`；其中中文业务片段用于给
          活动目录打分。
        - `scope_terms`：运行时术语扩展校验后的真实代码词，类型为
          `Sequence[str]`；命中活动目录名时额外加分。
        - `include_standalone_configs`：是否把传统布局的全局配置
          （`php/config/noble.php`、`php/conf/*.php`）作为独立候选分组，类型为
          `bool`。等级枚举必须打开，否则贵族等级表没有活动目录会被排除；
          普通配置取值保持默认 `False`，避免全局配置文件抢走领域分析器的答案。

    Returns:
        主活动目录的证据行。多个活动同时出现时按“中文业务锚点 + 配置/入口证据
        完整度 + 术语命中”选择主活动；只有配置文件、没有任何入口或服务代码，
        且中文锚点不足的目录会被排除，避免用别的活动配置答错问题。完全无法
        判断时返回 `None`。
    """
    scope = _scope_identifier(question, lines)
    if scope:
        return list(lines)
    roots: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        # 活动目录和传统 `php/config/*.php` 都参与分组；后者按文件名单独成组，
        # 这样 `noble.php` 的等级门槛不会被同时出现的活动配置覆盖。
        normalized_file = item.file.replace("\\", "/")
        activity = _config_file_root(normalized_file)
        if not activity or activity.casefold().endswith(".php"):
            continue
        # 默认只比较真实的 `crontab/logic/action_activity` 活动目录；传统
        # 全局配置只在等级枚举等明确需要配置结构的场景参与，避免影响
        # 领域分析器和配置取值的既有优先级。
        if not include_standalone_configs and not _activity_root(normalized_file):
            continue
        roots.setdefault(activity, []).append(item)
    if not roots:
        # 传统配置布局（`myapp/config/fupay.php`）没有活动目录可比较。是否
        # 继续解析由调用方决定：环境配置取值需要这类文件，活动配置取值则
        # 必须避免用无关配置文件抢答。
        return None
    if len(roots) == 1:
        return lines
    anchors = [
        value
        for value in extract_generic_anchors(question, max_anchors=32)
        if len(value) >= 2
    ]
    concepts = {item.name for item in detect_semantic_concepts(question)}
    normalized_terms = [
        re.sub(r"[^a-z0-9]", "", str(term).casefold())
        for term in scope_terms
        if len(str(term).strip()) >= 4
    ]
    normalized_terms = [term for term in normalized_terms if term]
    scored: List[Tuple[int, int, str, List[EvidenceLine], bool]] = []
    for root, root_lines in roots.items():
        normalized_root = re.sub(r"[^a-z0-9]", "", root.casefold())
        context = " ".join([root, *[item.content for item in root_lines]]).casefold()
        anchor_score = _question_anchor_score(context, anchors)
        context_concepts = {
            concept.name for concept in detect_semantic_concepts(context)
        }
        score = anchor_score + len(concepts.intersection(context_concepts)) * 6
        # 问题里的业务概念如果正是分组文件名（例如 `vip` 经术语校验后展开为
        # `noble`，对应 `config/noble.php`），说明这就是用户点名的配置。
        # 它比配置注释里的“等级/门槛”等泛化词更可靠，作为强标识单独加权。
        root_segments = {
            segment
            for segment in re.split(r"[^a-z0-9]+", root.casefold())
            if segment
        }
        if concepts.intersection(root_segments):
            score += 40
        # `VIP` 是业务侧对贵族体系的常用叫法，配置目录使用 `noble`。
        # 这里只按领域同义词加分，帮助贵族门槛题稳定落在 `config/noble`，
        # 不改变其他活动的排序。
        if "vip" in question.casefold() and "noble" in root_segments:
            score += 25
        # 展示词评分与锚点评分同一口径：只使用活动自身的中文文案和目录语义，
        # 让“公会寻宝”“游戏榜”这类营销名在多活动并列时仍能落到真实目录。
        score += _activity_alias_score(question, root_lines)
        structure_score = _activity_structure_score(question, root_lines)
        score += structure_score
        has_config = any(
            _is_config_definition_file(item.file) for item in root_lines
        )
        has_other = any(
            not _is_config_definition_file(item.file) for item in root_lines
        )
        if has_config:
            score += 12
        if has_other:
            score += 6
        # 扩展类名与目录名通常只差元音/词尾（`GameRankPrizeService` →
        # `gameRanking`）。同时比较后缀剥离后的字母骨架，才能让真实类名
        # 为活动目录提供高置信度佐证。
        term_hit = any(
            term in normalized_root
            or _compact_subsequence(term, normalized_root)
            for term in normalized_terms
        ) or any(
            _compact_subsequence(alias, normalized_root)
            for term in scope_terms
            for alias in _term_alias_variants(str(term))
        )
        if term_hit:
            score += 15
        # 目录名/配置文件名本身就是术语的一部分时（`noble` 出现在
        # `NewNobleScoreServices`、`loveBall` 出现在 `LoveBallLogic`），说明
        # 问题术语直接指向该配置。单独加权，避免中文展示词缺失时贵族、
        # 家族城堡这类全局配置被其他活动抢占。
        root_token = re.sub(
            r"[^a-z0-9]", "", root.rsplit("/", 1)[-1].casefold()
        )
        if len(root_token) >= 4 and any(
            root_token in re.sub(r"[^a-z0-9]", "", str(term).casefold())
            for term in normalized_terms
        ):
            score += 25
        # 检索相关度是区分“同名背景下真正被问到活动”的稳定信号：多个活动
        # 都有送礼阶段、奖池和配置注释时，中文锚点会打平。这里取单行最高
        # 相关度而不是求和：完整配置窗口会把同活动几十行低相关度证据加总，
        # 反而不相关的大活动会靠行数压过真正命中的小活动。
        try:
            relevance = max(
                [float(getattr(item, "score", 0.0) or 0.0) for item in root_lines]
                or [0.0]
            )
        except (TypeError, ValueError):
            relevance = 0.0
        if relevance > 0:
            score += min(12, int(relevance * 8))
        # 配置+入口代码齐全的目录只需存在业务相关度；只有配置文件的目录则要求
        # 至少命中一个四字业务片段，或命中已校验的活动术语，防止检索噪声里的
        # 无关活动配置抢答。
        # 结构强匹配（含 rank_prizes/levels+limit/allow_types 等）足以单独
        # 证明配置与问题意图一致，即使同活动入口文件尚未进入证据。
        qualified = (
            has_config and has_other and score > 0
        ) or (has_config and anchor_score >= 6) or (has_config and term_hit) or (
            has_config and structure_score >= 30
        )
        scored.append((score, len(root_lines), root, root_lines, qualified))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    qualified_candidates = [item for item in scored if item[4]]
    if not qualified_candidates:
        return None
    return qualified_candidates[0][3]


def _safe_literal_text(raw_value: str) -> str:
    """校验字符串字面量是否适合写入诊断事实。

    参数:
        - `raw_value`：正则从 PHP 配置行捕获的原始值，类型为 `str`；字符串值带有
          单引号或双引号。

    Returns:
        数字和布尔值原样返回；字符串仅保留代码常量引用、“多少美元/金币”这类
        面向用户的奖励标签、英文奖励/关卡名和短中文名称。URL、文件路径、翻译
        键、令牌、密钥和含地址符号的文本一律返回空字符串，避免配置解析器扩大
        敏感信息暴露面。
    """
    if not raw_value.startswith(("'", '"')):
        return raw_value
    inner = raw_value[1:-1].strip()
    if _SAFE_CODE_CONSTANT_PATTERN.fullmatch(inner) or _SAFE_REWARD_LABEL_PATTERN.fullmatch(inner):
        return inner
    if not inner or len(inner) > 60:
        return ""
    folded = inner.casefold()
    if any(fragment in folded for fragment in _UNSAFE_LABEL_FRAGMENTS):
        return ""
    if _SAFE_ENGLISH_LABEL_PATTERN.fullmatch(inner):
        return inner
    # 短中文名称（关卡名、物品名）用于回答“阶段名称/奖励名称”，长度上限
    # 防止把整段运营文案写进结论。
    if re.fullmatch(r"[\u4e00-\u9fff0-9·、（）()\- ]{2,40}", inner):
        return inner
    return ""


def _question_anchor_score(context: str, anchors: Sequence[str]) -> int:
    """计算源码上下文与问题业务片段的重合权重。

    参数:
        - `context`：配置行、所在数组标题和邻近中文注释的组合文本，类型为 `str`。
        - `anchors`：`extract_generic_anchors` 从问题生成的中文业务片段，类型为
          `Sequence[str]`。

    Returns:
        可直接用于候选排序的非负分值。只统计互不包含的最长片段：问题切片会
        产生大量重叠双子串（“年度/度金/金主”），如果逐个累加会让刚好包含公司
        名称的数组压过真正对应的配置键。四字以上完整片段权重仍高于两字核心词。
    """
    folded = context.casefold()
    matched: List[str] = []
    score = 0
    for anchor in sorted(anchors, key=len, reverse=True):
        value = anchor.casefold()
        if len(value) < 2 or value not in folded:
            continue
        if any(value in existing for existing in matched):
            continue
        matched.append(value)
        score += len(value) * (4 if len(value) >= 4 else 1)
    return score


_ENGLISH_PHRASE_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?: [A-Za-z][A-Za-z0-9]*){0,3}"
)


def _english_phrase_score(context: str, question: str) -> int:
    """计算问题中英文业务名与配置上下文的重合权重。

    参数:
        - `context`：配置行、数组标题和注释的组合文本，类型为 `str`。
        - `question`：用户原始问题，类型为 `str`；其中的英文活动名、关卡名和
          道具名用于提高对应行的排序。

    Returns:
        非负分值。完整多词短语（`Shell Beach`）权重高于单个单词，六字符以上
        单词才参与计分，避免 `pro`、`type` 这类通用字段名带来误命中。
    """
    folded = context.casefold()
    score = 0
    for phrase in _ENGLISH_PHRASE_PATTERN.findall(question or ""):
        normalized = phrase.strip().casefold()
        if " " in normalized:
            if len(normalized) >= 5 and normalized in folded:
                score += len(normalized) * 4
            continue
        if len(normalized) >= 6 and normalized in folded:
            score += len(normalized) * 2
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


# 用户问题中直接写出的点号配置路径，例如 `pool.rate`、`guess.bet.min`。
_POINTER_KEY_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*"
)


def _pointer_keys(question: str) -> List[str]:
    """提取问题中的点号配置路径。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        形如 `pool.rate` 的完整路径列表；没有点号路径时返回空列表。该结果只用于
        精确匹配配置键，不参与业务结论推断。
    """
    return _POINTER_KEY_PATTERN.findall(question or "")


def _scalar_list_values(content: str) -> List[Tuple[str, str]]:
    """解析单行标量数组中的数字值及其位置键。

    参数:
        - `content`：单行 PHP 配置源码，类型为 `str`；支持匿名数组
          `[1, 5, 100]` 和数字键数组 `1 => [10, 30]` 两种写法。

    Returns:
        `(位置键, 数值文本)` 列表，位置键从 1 开始；数字键数组保留原键并追加
        内层位置，例如 `1 => [10, 30]` 返回 `("1.1", "10")`、`("1.2", "30")`。
        非纯数字数组返回空列表，防止把嵌套结构或字符串列表当成配置值。
    """
    stripped = content.lstrip()
    keyed = _KEYED_SCALAR_LIST_PATTERN.match(stripped)
    if keyed:
        parts = [part.strip() for part in keyed.group(2).split(",") if part.strip()]
        return [
            (f"{keyed.group('key')}.{position}", value)
            for position, value in enumerate(parts, 1)
        ]
    anonymous = _SCALAR_LIST_PATTERN.match(stripped)
    if anonymous:
        parts = [part.strip() for part in anonymous.group(1).split(",") if part.strip()]
        return [
            (str(position), value)
            for position, value in enumerate(parts, 1)
        ]
    return []


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
                # 活动配置里的冒险点、关卡和活动名大量使用英文（`Shell Beach`、
                # `Bubble*7 days`）。中文锚点不覆盖这些词，问题原文出现英文名时
                # 必须按英文短语提高对应行的权重，否则同名等级数组会抢先。
                total_score += _english_phrase_score(context, question)
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
                # 中文业务意图与常见英文字段名对齐（比例→rate、库存→stock 等）。
                # 只提高该键的排序权重，最终结论仍然只复述源码字面值。
                folded_key = key.casefold()
                for intent, aliases in _KEY_INTENT_ALIASES.items():
                    if intent not in question:
                        continue
                    if any(alias in folded_key for alias in aliases):
                        total_score += 30
                        break
                # 用户直接写出配置键时优先精确匹配路径末级键，而不是依赖中文
                # 注释；该规则同样适用于未预置的新配置。
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", question):
                    total_score += 40
                # 用户写出完整点号路径（`pool.rate`）时，路径末级键本身可能
                # 很短或与同文件其他键重名，必须按完整路径加分才能精确命中。
                key_path_text = ".".join((*group_path, key))
                if any(
                    key_path_text == dotted or key_path_text.endswith(f".{dotted}")
                    for dotted in _pointer_keys(question)
                ):
                    total_score += 50
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
            # 表达式日期：`'stime' => $_dateTime->modify('2026-09-23 12:00:00')...`。
            # 这类值不是数字字面量，但同样是可核对的活动配置；只有问题涉及时间时
            # 才提高权重，避免把无关日期混入金额类问题的结论。
            assignment = _ASSIGNMENT_KEY_PATTERN.match(stripped)
            date_match = _DATE_LITERAL_PATTERN.search(item.content)
            if assignment and date_match and not any(
                literal.group("key") == assignment.group("key")
                for literal in _LITERAL_ASSIGNMENT_PATTERN.finditer(item.content)
            ):
                key = assignment.group("key")
                value_text = date_match.group(1)
                inline = _inline_comment(item.content)
                context = " ".join(
                    value
                    for value in (
                        item.content,
                        inline,
                        " ".join(recent_comments),
                        " ".join(entry.key for entry in stack),
                    )
                    if value
                )
                score = _question_anchor_score(" ".join((inline, key)), anchors)
                score += _question_anchor_score(context, anchors)
                # “提前多少秒截止”问的是数字阈值，不是活动起止时间；只有问题
                # 明确出现时间/日期/起止/开始/结束/开放时才提高日期表达式权重。
                if any(
                    word in question
                    for word in ("时间", "日期", "起止", "开始", "结束", "开放")
                ):
                    score += 50
                if score > 0:
                    candidates.append(
                        _ConfigLiteralCandidate(
                            line=item,
                            group_path=tuple(entry.key for entry in stack),
                            key=key,
                            value_text=value_text,
                            category="配置日期",
                            score=score,
                            direct_score=0,
                            key_score=0,
                            path_score=0,
                            context=context,
                            source_comment=inline,
                        )
                    )
            # 标量数组（`[1, 5, 100]`、`1 => [10, 30]`）没有键名，但同样是
            # 可核对的配置值。位置键用于在“第 2 档/2 级”提问时收窄到正确条目。
            scalar_group_path = tuple(entry.key for entry in stack)
            scalar_inline = _inline_comment(item.content)
            for position_key, scalar_value in _scalar_list_values(item.content):
                scalar_context = " ".join(
                    value
                    for value in (
                        item.content,
                        scalar_inline,
                        " ".join(scalar_group_path),
                        " ".join(recent_comments),
                    )
                    if value
                )
                scalar_score = _question_anchor_score(scalar_context, anchors)
                folded_path = " ".join((*scalar_group_path, position_key)).casefold()
                for intent, aliases in _KEY_INTENT_ALIASES.items():
                    if intent in question and any(
                        alias in folded_path for alias in aliases
                    ):
                        scalar_score += 30
                        break
                if scalar_score <= 0:
                    continue
                candidates.append(
                    _ConfigLiteralCandidate(
                        line=item,
                        group_path=scalar_group_path,
                        key=position_key,
                        value_text=scalar_value,
                        category="配置数值",
                        score=scalar_score,
                        direct_score=0,
                        key_score=0,
                        path_score=0,
                        context=scalar_context,
                        source_comment=scalar_inline,
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
        一到十之间的序号；没有明确序号时返回 `None`。支持“第 3 档”和“2 级”
        两种写法，但“前 10 名”“四个等级”属于数量描述，不会被当成序号。该值
        只用于在同名重复配置中选择正确条目，不参与业务结论推断。
    """
    explicit = re.search(
        r"第\s*([一二三四五六七八九十\d]+)\s*(?:个|档|级|名|项|轮)",
        question,
    )
    if explicit:
        raw = explicit.group(1)
    else:
        raw = None
        for match in re.finditer(
            r"(?:^|(?<=[^前]))([一二三四五六七八九十\d]+)\s*(?:档|级|名|轮)",
            question,
        ):
            # “前 10 名”是数量范围而不是单个序号，跳过这类写法。
            if match.start() > 0 and question[match.start() - 1] == "前":
                continue
            raw = match.group(1)
            break
        if raw is None:
            return None
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


def _ordinal_selected(
    selected: Sequence[_ConfigLiteralCandidate],
    question: str,
) -> List[_ConfigLiteralCandidate]:
    """按问题中的等级序号收窄重复出现的同名配置键。

    参数:
        - `selected`：`_select_candidates` 选出的配置候选，类型为
          `Sequence[_ConfigLiteralCandidate]`。
        - `question`：用户原始问题，类型为 `str`；用于识别“第 3 档/2 级”等序号。

    Returns:
        每个同名键只保留序号对应的那一次出现，其余键保持不变。序号越界时保留
        该键的全部出现，避免因为配置证据窗口缺行而返回空答案。
    """
    ordinal = _question_ordinal(question)
    if ordinal is None or ordinal <= 0:
        return list(selected)
    # 匿名等级列表会把等级序号写进路径（`levels.1.value`、`levels.2.value`），
    # 同一个业务键因此被拆成多个组。按“去掉数字段”的稳定路径归并，才能把
    # 各等级的同名键按源码顺序对齐用户点名的第 N 项。
    by_key: Dict[Tuple[Tuple[str, ...], str], List[_ConfigLiteralCandidate]] = {}
    for item in selected:
        stable_path = tuple(
            segment for segment in item.group_path if not segment.isdigit()
        )
        by_key.setdefault((stable_path, item.key), []).append(item)
    narrowed: List[_ConfigLiteralCandidate] = []
    for items in by_key.values():
        ordered = sorted(items, key=lambda item: item.line.line)
        if len(ordered) >= ordinal:
            narrowed.append(ordered[ordinal - 1])
        else:
            narrowed.extend(ordered)
    return sorted(narrowed, key=lambda item: (item.line.file, item.line.line))


def _intent_expanded_selection(
    selected: Sequence[_ConfigLiteralCandidate],
    candidates: Sequence[_ConfigLiteralCandidate],
    question: str,
) -> List[_ConfigLiteralCandidate]:
    """在多意图问题中补齐每个业务意图对应的最相关配置键。

    参数:
        - `selected`：当前按单一数组组选出的候选，类型为
          `Sequence[_ConfigLiteralCandidate]`。
        - `candidates`：同一次解析生成的全部候选，类型为
          `Sequence[_ConfigLiteralCandidate]`。
        - `question`：用户原始问题，类型为 `str`；包含两个及以上业务意图词时
          才扩展，避免单意图问题引入无关数组。

    Returns:
        合并后的候选列表。多意图问题（“多少燃料、多少碎片、前几名”）通常跨
        多个嵌套数组，单一数组组无法同时覆盖；这里为每个意图补一个最相关键，
        仍然只输出源码中真实存在的字面量。
    """
    intent_words = [word for word in _KEY_INTENT_ALIASES if word in question]
    if len(intent_words) < 2:
        return list(selected)
    result: Dict[Tuple[str, int, Tuple[str, ...], str], _ConfigLiteralCandidate] = {}
    for item in selected:
        result[(item.line.file, item.line.line, item.group_path, item.key)] = item
    for word in intent_words:
        aliases = _KEY_INTENT_ALIASES[word]
        picks = [
            item
            for item in candidates
            if any(alias in item.key.casefold() for alias in aliases)
        ]
        if not picks:
            continue
        best = max(picks, key=lambda item: item.score)
        result[(best.line.file, best.line.line, best.group_path, best.key)] = best
    return sorted(
        result.values(), key=lambda item: (item.line.file, item.line.line)
    )[:12]


# 配置里对“档位”的显式命名。问题点名这些名称时，答案位于对应子数组，
# 同文件里的总碎片数、总比例只是汇总值，不能替代该档位的明细。
_NAMED_TIER_TOKENS: Dict[str, Tuple[str, ...]] = {
    "固定档": ("fixed", "top_fixed"),
    "保底档": ("guarantee", "minimum_guarantee"),
    "惊喜档": ("surprise",),
}


def _named_tier_aliases(question: str) -> Tuple[str, ...]:
    """返回问题中唯一点名档位对应的字段别名。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        只点名一个档位词时返回该档位的英文字段别名；没有点名或同时点名多个
        档位时返回空元组。多个档位需要分别回答，不能被单一子数组收窄。
    """
    matched = [
        aliases
        for token, aliases in _NAMED_TIER_TOKENS.items()
        if token in question
    ]
    return matched[0] if len(matched) == 1 else ()


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
    # 问题直接写出 `pool.rate`、`guess.bet.min` 这类点号配置路径时，按完整路径
    # 精确返回对应键，不能被同文件里分值更高的其他数字候选抢走。
    dotted_keys = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*",
        question,
    )
    if dotted_keys:
        exact = []
        for item in candidates:
            key_path = ".".join((*item.group_path, item.key))
            if any(
                key_path == dotted or key_path.endswith(f".{dotted}")
                for dotted in dotted_keys
            ):
                exact.append(item)
        if exact:
            return sorted(
                exact, key=lambda item: (item.line.file, item.line.line)
            )
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
    # “最大的 limit/最高比例”要求在同一配置键的多次出现中取极值，而不是取
    # 相关度最高的一行。早先直接返回 best 会命中第一个分档（例如 limit=0）。
    if "最大" in question or "最高" in question:
        same_key = [
            item
            for item in candidates
            if item.key == best.key
            and item.group_path == best.group_path
            and _numeric_value(item.value_text) is not None
        ]
        if same_key:
            return [max(same_key, key=lambda item: _numeric_value(item.value_text) or 0.0)]
    if "最小" in question or "最低" in question:
        same_key_min = [
            item
            for item in candidates
            if item.key == best.key
            and item.group_path == best.group_path
            and _numeric_value(item.value_text) is not None
        ]
        if same_key_min:
            return [min(same_key_min, key=lambda item: _numeric_value(item.value_text) or 0.0)]
    tier_aliases = _named_tier_aliases(question)
    if tier_aliases:
        tier_candidates = [
            item
            for item in candidates
            if any(
                alias in item.key.casefold()
                or alias in " ".join(item.group_path).casefold()
                for alias in tier_aliases
            )
        ]
        if tier_candidates:
            # 档位明细可能嵌在总览数组下；取路径最深的命中，避免用同文件的
            # 总碎片数、总比例回答单个档位。
            deepest = max(len(item.group_path) for item in tier_candidates)
            tier_candidates = [
                item
                for item in tier_candidates
                if len(item.group_path) == deepest
            ]
            return _ordinal_selected(
                sorted(
                    tier_candidates, key=lambda item: (item.line.file, item.line.line)
                ),
                question,
            )[:16]
    # 用户用英文键名点名多个配置键（`year 和 flow_type 是什么`）时，应同时
    # 返回这些键，不能因为其中一个概念分更高就只留一个。
    bare_keys = {
        token.casefold()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)
    }
    explicit_keys = [item for item in candidates if item.key.casefold() in bare_keys]
    if explicit_keys:
        return _ordinal_selected(
            sorted(explicit_keys, key=lambda item: (item.line.file, item.line.line)),
            question,
        )[:16]
    targeted = any(value in question for value in ("提前", "截止"))
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
    # 开关。先于业务意图排序选出全部开关，避免“价格”等意图词把 rules.price
    # 这类普通数值键提到前面，导致结论只回答了一个数字开关。
    if any(token in question for token in ("有没有", "是否", "有无")):
        enabled = [
            item
            for item in candidates
            if item.key.casefold() == "enabled"
            and item.line.file == candidates[0].line.file
        ]
        if enabled:
            best_enabled = max(enabled, key=lambda item: item.score)
            parent_path = best_enabled.group_path[:-1]
            siblings = [
                item
                for item in enabled
                if item.group_path[:-1] == parent_path
                and item.line.file == best_enabled.line.file
            ]
            return sorted(
                siblings, key=lambda item: (item.line.file, item.line.line)
            )
    selected_sorted = sorted(selected, key=lambda item: (item.line.file, item.line.line))
    # “2 级/第 3 档”只保留对应序号；多意图问题再补齐其他业务意图的最佳键。
    narrowed = _ordinal_selected(selected_sorted, question)
    return _intent_expanded_selection(narrowed, candidates, question)


# 数据表模型调用：仓库中数据库模型类统一以 `T` 开头，读取方法以 `get/find/`
# `load/select` 开头，幂等判断以 `exists` 开头。这里只捕获类名和方法名，不
# 捕获参数和字符串字面量，避免把查询值或配置值写入结论。
_DB_MODEL_CALL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_\\])(?P<model>T[A-Za-z][A-Za-z0-9_\\]*)::"
    r"(?P<method>(?:get|find|load|select|exists)[A-Za-z0-9_]+)\("
)


def execution_source_facts(
    lines: Sequence[EvidenceLine],
    question: str,
) -> List[Dict[str, Any]]:
    """从活动服务代码中提取数据来源与结算幂等事实。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`；
          需要包含同活动的服务/控制器完整内容。
        - `question`：用户原始业务问题，类型为 `str`；只有询问“从哪里读取/
          数据源/结算表/避免重复”时才生成事实。

    Returns:
        最多两条事实，分别说明活动期配置的数据库读取入口和结算防重方法。
        没有匹配的数据表模型调用时返回空列表，由上层继续报告证据缺口。结论只
        含模型类名和方法名，不含参数、查询条件或任何配置值。
    """
    source_intent = any(
        token in question
        for token in ("从哪里读取", "数据源", "来源", "哪里读", "从哪里", "读取")
    )
    idempotent_intent = any(
        token in question for token in ("重复", "幂等", "避免", "已结算", "已写入")
    )
    if not source_intent and not idempotent_intent:
        return []
    calls: List[Tuple[str, str, EvidenceLine]] = []
    seen: set = set()
    for item in sorted(lines, key=lambda row: (row.file, row.line)):
        for match in _DB_MODEL_CALL_PATTERN.finditer(item.content or ""):
            model = match.group("model").lstrip("\\")
            method = match.group("method")
            key = (model.casefold(), method.casefold())
            if key in seen:
                continue
            seen.add(key)
            calls.append((model, method, item))
            if len(calls) >= 8:
                break
        if len(calls) >= 8:
            break
    facts: List[Dict[str, Any]] = []
    if source_intent:
        reads = [
            (model, method, item)
            for model, method, item in calls
            if method.casefold().startswith(("get", "find", "load", "select"))
        ]
        if reads:
            model, method, item = reads[0]
            facts.append(
                {
                    "category": "配置数据来源",
                    "statement": (
                        f"活动期配置通过数据库表模型 `{model}::{method}` 动态读取；"
                        f"`config.php` 只保留基础配置项，具体期数来自数据库，"
                        f"因此配置文件本身行数很少。"
                    ),
                    "evidence_refs": [item.ref],
                }
            )
    if idempotent_intent:
        checks = [
            (model, method, item)
            for model, method, item in calls
            if method.casefold().startswith("exists")
        ]
        if checks:
            model, method, item = checks[0]
            facts.append(
                {
                    "category": "结算幂等",
                    "statement": (
                        f"结算前通过 `{model}::{method}` 查询该期是否已有记录；"
                        f"已存在时跳过本次写入，从而避免重复结算。"
                    ),
                    "evidence_refs": [item.ref],
                }
            )
    return facts


def _configuration_literal_facts(
    lines: Sequence[EvidenceLine],
    question: str,
    scope_terms: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """返回目录范围内的配置数值与开关事实。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`。
        - `scope_terms`：术语扩展校验后的真实代码词，类型为 `Sequence[str]`；
          中文活动名无法直接匹配目录时用于定位目标活动配置。

    Returns:
        最多十二条带真实源码引用的配置事实；没有可唯一确定的活动目录或配置
        字面量时返回空列表，让调用方继续使用其他分析器或明确报告证据缺口。
    """
    scope = _scope_identifier(question, lines)
    # 中文活动名（例如“公会寻宝”）无法直接变成英文目录名。先按扩展词定位
    # 活动目录；扩展词不可用时，由 `scoped_config_evidence` 选择与问题最相关
    # 的主活动，避免多活动证据直接放弃配置回答。
    scoped_lines = (
        list(lines)
        if scope
        else scoped_config_evidence(lines, question, scope_terms)
    )
    if not scoped_lines:
        return []
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


_DECISION_INTENT_TOKENS = ("判定", "逻辑", "按什么字段", "怎么算", "如何计算", "入口", "在哪")
_DECISION_FUNCTION_PATTERN = re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)")
_DECISION_CLASS_PATTERN = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")


def activity_decision_source_facts(
    lines: Sequence[EvidenceLine],
    question: str,
    activity_link: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """从已链接活动的实现代码提取等级判定入口。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`；
          需要包含活动知识库补入的服务/控制器完整内容。
        - `question`：用户原始业务问题，类型为 `str`；只有询问判定逻辑、入口或
          按什么字段计算时才生成事实。
        - `activity_link`：实体链接摘要，类型为 `Optional[Dict[str, Any]]`；
          需提供 `activity_id`、`level_key`、`level_field` 和 `related_files`。
          为空时表示未链接，直接返回空列表，不能用其他活动服务代替。

    Returns:
        最多一条事实，说明读取 `<活动>.<等级键>` 的类与方法入口；找不到类、
        方法或配置文件读取点时返回空列表，由上层报告证据缺口。事实只包含
        类名、方法名和配置路径，不写入方法入参值或源码正文。
    """
    if not activity_link:
        return []
    if not any(token in (question or "") for token in _DECISION_INTENT_TOKENS):
        return []
    activity_id = str(activity_link.get("activity_id") or "").strip()
    level_key = str(activity_link.get("level_key") or "").strip()
    level_field = str(activity_link.get("level_field") or "").strip()
    if not activity_id or not level_key:
        return []
    related = {
        str(path or "").replace("\\", "/").casefold()
        for path in (activity_link.get("related_files") or [])
    }

    def reads_level_config(item: EvidenceLine) -> bool:
        """判断证据行是否读取链接活动等级配置。

        参数:
            - `item`：一条源码证据，类型为 `EvidenceLine`。

        Returns:
            行内出现 `Config::get('<活动>.<等级键>')`，或同时出现等级键与门槛
            字段时返回 `True`。只做字面匹配，不执行 PHP 表达式。
        """
        content = item.content or ""
        if re.search(
            rf"Config::get\(\s*['\"]{re.escape(activity_id)}\s*\.\s*"
            rf"{re.escape(level_key)}['\"]",
            content,
        ):
            return True
        if not re.search(rf"['\"]{re.escape(level_key)}['\"]", content):
            return False
        if not level_field:
            return True
        return bool(re.search(rf"['\"]{re.escape(level_field)}['\"]", content))

    candidates = [
        item
        for item in sorted(lines, key=lambda row: (row.file, row.line))
        if (not related or item.file.replace("\\", "/").casefold() in related)
        and reads_level_config(item)
    ]
    for read in candidates[:3]:
        file_lines = [item for item in lines if item.file == read.file]
        class_item = next(
            (
                item
                for item in file_lines
                if _DECISION_CLASS_PATTERN.search(item.content or "")
            ),
            None,
        )
        methods = [
            (item, match.group(1))
            for item in file_lines
            if (match := _DECISION_FUNCTION_PATTERN.search(item.content or ""))
        ]
        if class_item is None or not methods:
            continue
        class_name = _DECISION_CLASS_PATTERN.search(class_item.content or "").group(1)
        # 入口方法优先选择名字包含等级键（`getLevel`），避免把初始化用的
        # `initCfg` 当成用户询问的判定入口。
        preferred = next(
            (
                (item, name)
                for item, name in methods
                if level_key.removesuffix("s") in name.casefold()
            ),
            None,
        )
        method_item, method_name = preferred or methods[0]
        target = f"{activity_id}.{level_key}"
        statement = (
            f"等级判定入口为 `{class_name}::{method_name}`，按配置 `{target}` 的 "
            f"`{level_field or '门槛'}` 门槛计算。"
        )
        return [
            {
                "category": "等级判定入口",
                "statement": statement,
                "evidence_refs": [
                    read.ref,
                    method_item.ref,
                    class_item.ref,
                ],
            }
        ]
    return []


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
        # 布尔配置保留源码字面值，同时给出中文状态：只写“开启”会丢失问题中
        # 显式点名的 true/false 取值，无法核对代码与描述是否一致。
        if value_text.casefold() == "false":
            rendered = "false（未开启）"
        elif value_text.casefold() == "true":
            rendered = "true（开启）"
        else:
            rendered = value_text
        # `enabled=0/1` 是活动开关最常见的写法；结论里直接给出“未开启/开启”，
        # 避免让用户自己把 0/1 翻译成业务状态。
        if key_path.rsplit(".", 1)[-1].casefold() == "enabled":
            if value_text.strip() in {"0", "0.0"}:
                rendered = f"{value_text}（未开启）"
            elif value_text.strip() in {"1", "1.0"}:
                rendered = f"{value_text}（开启）"
        values.append(f"`{key_path}={rendered}`")
    if not values:
        return ""
    # `adminConfig=true` 是这类活动配置的通用标记：等级、排名等奖励明细由
    # 管理后台维护并存入数据库，PHP 文件只保留活动基础参数。只有当前证据中
    # 确实没有等级/排名/奖励配置时才这样说明，避免覆盖真实的 PHP 奖励表。
    admin_config = any(
        "adminconfig" in str(fact.get("source_key") or "").casefold()
        and str(fact.get("source_value") or "").casefold() == "true"
        for fact in facts
    )
    reward_keys = {
        str(fact.get("source_key") or "").casefold() for fact in facts
    }
    # 按路径段判断是否存在内联奖励表，避免 `level_type` 这类类型开关被
    # 子串误判成奖励配置。
    has_inline_rewards = any(
        segment in {"levels", "prizes", "prize", "rewards", "reward", "rank_prizes", "ranks"}
        for key in reward_keys
        for segment in key.split(".")
    )
    prefix = ""
    if admin_config and not has_inline_rewards:
        prefix = (
            "该活动配置标记为 `adminConfig=true`：等级与排名奖励由管理后台"
            "维护并写入数据库，PHP 配置数组未包含这些奖励明细。"
        )
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
        prefix
        + "已从活动配置源码确认："
        + "；".join(values)
        + "。以上结论以代码字面值为准"
        + suffix
        + "。"
    )


__all__ = [
    "_configuration_fact_conclusion",
    "_configuration_literal_facts",
    "execution_source_facts",
]
