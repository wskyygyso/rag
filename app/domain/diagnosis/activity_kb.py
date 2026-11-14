"""项目活动知识库的离线构建、实体链接与相关实现文件选择。

模块职责：
- 从被诊断仓库的真实目录、活动配置和中文语言包派生活动记录；
- 把用户问题链接到唯一活动，或明确返回未链接/歧义；
- 为链接结果挑选同活动服务、控制器与全局配置实现文件。

边界与安全约束：
- 只读取活动 `config.php`、`php/config/*.php`、`php/conf/*.php` 及同名
  `php/lang/zh/*.php` 语言包，不访问业务数据库、Redis、日志或线上服务；
- 别名只能来自真实数据：目录名、配置文件中文文案和语言包值，禁止写入活动专属
  硬编码映射；
- 知识库只保存结构标记和匹配词，不保存密钥、URL、图片地址或完整源码；
- 链接分差不足时返回歧义结果，调用方必须按“未链接”处理，不能猜测活动。
"""

from dataclasses import dataclass
import os
import re
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# 单个配置文件读取上限。活动配置通常几十 KB，超过该值的文件多为导出数据或
# 多语言全量文案，继续读取会显著拖慢一次全仓构建。
MAX_CONFIG_BYTES = 400 * 1024
# 每个活动保留的低置信中文片段上限，避免异常长配置文件撑大内存。
MAX_CONTEXT_ZH = 200
# 每个活动保留的审计引用条数。
MAX_SOURCE_REFS = 6
# 全仓最多构建的活动记录数；活动目录和全局配置文件合计通常不超过 350 个。
MAX_ACTIVITIES = 400
# 单个活动保留的结构化等级条目上限。等级表通常不超过 20 条，上限只防止异常
# 配置把整个文件当成独立等级写入内存。
MAX_LEVELS = 40
# 单个活动保留的奖励定义条数上限。奖励表常按 100..503 编号，120 条覆盖真实活动。
MAX_PRIZES = 120
# 单个等级保留的奖励引用条数上限，避免异常数组把整份奖品表挂到同一等级。
MAX_LEVEL_PRIZE_REFS = 12
# 等级条目中可作为门槛的字段名，按优先级排列；只接受数字或 getByTestMode 成对值。
LEVEL_THRESHOLD_KEYS = ("limit", "score", "value", "need", "require", "gift_value")
# 等级条目中可能保存奖励引用的数组键。
LEVEL_PRIZE_KEYS = ("prizes", "top1_prizes")
# 后台活动登记的路径优先级：后台先查 crontab 配置，再查 Logic boot 配置。
BACKEND_MARKER_PRIORITY = {
    "crontab": 0,
    "Logic": 1,
    "action_activity": 2,
    "config": 3,
    "conf": 4,
}
# 活动目录标记。它们是仓库中承载活动配置与实现的真实目录名。
ACTIVITY_MARKERS = ("Logic", "crontab", "action_activity")
# 全局配置目录标记。贵族、支付等配置没有活动目录，按文件名干单独成组。
CONFIG_MARKERS = ("config", "conf")
# 构建时跳过的目录，避免把第三方库、测试和版本库内容当作活动。
SKIP_DIR_NAMES = {"vendor", "lib", "tests", "test", "phpunit", ".git", "node_modules"}
# 中文展示词：连续中文片段，长度 2 至 40。
_ZH_PHRASE_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,40}")
# 方括号或井号包裹的活动文案，例如 `【魔法炼金屋】`、`#心动的旋律#`。
_BRACKET_ALIAS_PATTERN = re.compile(r"【([^】\n]{2,20})】|#([^#\n]{2,20})#")
# 配置中的显式中文标题字段。
_TITLE_PATTERN = re.compile(
    r"['\"](?:title|app_nick)['\"]\s*=>\s*['\"]([^'\"\n]{2,60})['\"]"
)
# 等级名称字段：`'name' => ['zh' => '业余', 'en' => 'amateur']`。
_LEVEL_NAME_PATTERN = re.compile(
    r"['\"](?:zh|en)['\"]\s*=>\s*['\"]([^'\"\n]{1,40})['\"]"
)
# 通用中文停用词。它们只表达问句结构，不指向任何活动；命中时不得给活动加分。
GENERIC_ZH_TERMS = {
    "活动", "等级", "门槛", "奖励", "配置", "时间", "日期", "开始", "结束",
    "什么", "多少", "分别", "哪些", "所有", "每个", "阶段", "档位", "排名",
    "名次", "金币", "美元", "测试", "正式", "环境", "开放", "截止", "用户",
    "玩家", "系统", "数据", "说明", "内容", "名称", "中文", "英文", "几个",
    "几级", "几档", "一次", "支持", "需要", "获得", "查看", "详情",
    # 泛化称谓：只说明角色，不指向具体活动。`鉴定大师` 若因“大师”命中某个
    # 活动，会把未知活动答成错误配置，因此这类片段不参与别名得分。
    "大师", "专家", "达人", "代言人", "榜单",
}
# 英文停用词：语言包里大量普通英文，不参与活动目录匹配。
GENERIC_EN_TERMS = {
    "the", "and", "for", "with", "you", "your", "this", "that", "from",
    "have", "has", "are", "not", "all", "can", "will", "info", "error",
    "success", "lang", "config", "conf", "php", "html", "http", "https",
}


@dataclass(frozen=True)
class ActivityLevel:
    """描述活动配置中的一条等级（档位）记录。

    属性说明：
    - `ordinal`：等级序号，优先取配置中的 `id`，缺失时按数组声明顺序补 1..n；
    - `threshold`：门槛的展示值，普通数字保留原字面值，`getByTestMode` 成对值
      渲染为 `测试 X / 正式 Y`；
    - `name`：等级名称，来自 `name.zh` 或字符串 `name`；没有名称时为空字符串；
    - `prize_ids`：该等级引用的奖励 id，来自 `prizes`/`top1_prizes` 数组；
    - `source_ref`：等级条目起始行的 `文件:行号`，用于事实审计。
    """

    ordinal: int
    threshold: str = ""
    name: str = ""
    prize_ids: Tuple[str, ...] = ()
    source_ref: str = ""


@dataclass(frozen=True)
class ActivityPrize:
    """描述活动配置顶层奖励定义表中的一条奖励。

    属性说明：
    - `prize_id`：`prizes` 映射键（与条目内 `id` 一致），等级表通过它引用；
    - `name`：奖励可读名称，来自 `name` 或 `name.zh`；没有名称时为空字符串；
    - `type`/`num`/`days`：奖励类型、数量和有效期天数，只保存在知识库中，
      本阶段不进入用户结论；
    - `source_ref`：奖励定义起始行的 `文件:行号`，用于事实审计。
    """

    prize_id: str
    name: str = ""
    type: str = ""
    num: str = ""
    days: str = ""
    source_ref: str = ""


@dataclass(frozen=True)
class ActivityRecord:
    """描述一个可从仓库数据重建的活动或全局配置分组。

    属性说明：
    - `activity_id`：活动目录名或全局配置文件名干，是链接和文件选择的主键；
    - `marker`：`Logic`/`crontab`/`action_activity`/`config`/`conf`，说明来源布局；
    - `config_file`：项目内相对路径，完整窗口和事实引用都以它为准；
    - `aliases_zh`：高置信中文展示词，来自标题、等级名称和方括号/井号文案；
    - `context_zh`：低置信中文片段，来自注释和普通文案；
    - `aliases_en`：目录词根和语言包中的英文标识，例如 `noble`、`VIP`；
    - `level_names`：等级中文/英文名称，用于“某等级叫什么”类问题；
    - `level_count`：等级条目数量，只用于意图匹配，不直接生成结论；
    - `level_key`：等级数组键名（`level` 或 `levels`）；
    - `level_field`：等级门槛字段（`score`、`value` 或 `limit`）；
    - `stime`/`etime`：配置中的起止时间字面量；
    - 其余布尔字段表示配置中是否存在对应结构，用于与问题意图对齐；
    - `volume`/`year`：活动期次与年份字面值，例如 `3`、`2023`；
    - `vol_ref`/`year_ref`：期次与年份所在行 `文件:行号`，用于事实审计；
    - `levels`：结构化等级表，保存序号、门槛、名称和奖励引用；
    - `prizes`：顶层奖励定义表，供等级奖励引用解析出可读名称；
    - `backend_registered`：是否命中后台活动登记路径；
    - `backend_config_file`：后台读取的配置路径，未登记时为空字符串；
    - `source_refs`：`文件:行号` 审计引用，供报告展示链接依据。
    """

    activity_id: str
    marker: str
    config_file: str
    aliases_zh: Tuple[str, ...] = ()
    context_zh: Tuple[str, ...] = ()
    aliases_en: Tuple[str, ...] = ()
    level_names: Tuple[str, ...] = ()
    level_count: int = 0
    level_key: str = ""
    level_field: str = ""
    stime: str = ""
    etime: str = ""
    has_rank_prizes: bool = False
    has_allow_types: bool = False
    has_surprise: bool = False
    has_jump_switch: bool = False
    env_pair_count: int = 0
    volume: str = ""
    year: str = ""
    vol_ref: str = ""
    year_ref: str = ""
    levels: Tuple[ActivityLevel, ...] = ()
    prizes: Tuple[ActivityPrize, ...] = ()
    backend_registered: bool = False
    backend_config_file: str = ""
    source_refs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ActivityLink:
    """描述一次“问题 → 活动”的链接结果。

    属性说明：
    - `record`：得分最高的活动记录；
    - `score`：链接总分，只用于选择活动，不进入业务结论；
    - `matched_terms`：命中的目录名、别名或等级名，供审计解释；
    - `ambiguous`：最高分与第二名分差不足时为 `True`，调用方必须按未链接处理；
    - `feedback_alias`：本次链接由哪条人工登记别名触发，未命中别名时为空字符串。
    """

    record: ActivityRecord
    score: int
    matched_terms: Tuple[str, ...] = ()
    ambiguous: bool = False
    feedback_alias: str = ""


# 单进程缓存：`(仓库根, 项目)` → `(代码指纹, 活动记录)`。缓存只保存派生结构，
# 不保存源码正文；代码指纹变化后整组重建。
_KNOWLEDGE_CACHE: Dict[Tuple[str, str], Tuple[str, Tuple[ActivityRecord, ...]]] = {}


def _default_read_text(root: str, project: str) -> Callable[[str], str]:
    """创建默认的只读文件读取函数。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`；对应根目录下的项目子目录。

    Returns:
        接收项目内相对路径并返回 UTF-8 文本的函数。文件不存在或超过
        `MAX_CONFIG_BYTES` 时抛出 `OSError`/`ValueError`，由调用方跳过该文件，
        避免异常配置中断整个知识库构建。
    """

    base = os.path.join(root, project)

    def read_text(relative_path: str) -> str:
        """读取一个项目内相对路径的 UTF-8 文本。

        参数:
            - `relative_path`：项目内相对路径，类型为 `str`；不接受绝对路径。

        Returns:
            文件完整文本。文件不是普通文件、超出大小上限或编码损坏时抛出异常，
            由构建函数按单文件降级。
        """
        candidate = relative_path.replace("\\", "/").strip("/")
        if not candidate or candidate.startswith("..") or os.path.isabs(candidate):
            raise ValueError("非法知识库读取路径")
        full_path = os.path.join(base, *candidate.split("/"))
        if not os.path.isfile(full_path):
            raise OSError("知识库文件不存在")
        if os.path.getsize(full_path) > MAX_CONFIG_BYTES:
            raise ValueError("知识库文件超过读取上限")
        with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    return read_text


def _normalize_zh_alias(value: str) -> str:
    """把配置文案规范化为可比较的中文别名。

    参数:
        - `value`：来自配置或语言包的原始文案，类型为 `str`。

    Returns:
        去掉 `%s`、`#`、方括号和首尾标点后只保留中英文与数字的片段；含 URL、
        图片路径、邮箱或翻译键的文本返回空字符串，避免扩大敏感信息暴露面。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    for fragment in ("http", "://", ".png", ".jpg", ".jpeg", ".gif", "@", "lang_map"):
        if fragment in lowered:
            return ""
    cleaned = re.sub(r"%[sd]", "", text)
    cleaned = re.sub(r"[#【】\[\]（）()：:，,。.！!？?\-_/\\|]", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    if not re.search(r"[\u4e00-\u9fff]", cleaned):
        return ""
    if len(cleaned) < 2 or len(cleaned) > 40:
        return ""
    return cleaned


def _extract_english_tokens(text: str) -> set:
    """从语言包或标题中提取可比较的英文标识。

    参数:
        - `text`：配置或语言包文本，类型为 `str`。

    Returns:
        长度至少三个字符、非通用停用词的英文标识集合。全部大写的缩写（如
        `VIP`）保留，方便由数据建立业务别名；普通英文只保留长度不少于四个
        字符的词，避免噪声。
    """
    tokens = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9]{2,40}", text or ""):
        folded = raw.casefold()
        if folded in GENERIC_EN_TERMS:
            continue
        if len(folded) >= 4 or (raw.isupper() and len(folded) >= 3):
            tokens.add(folded)
    return tokens


def _line_number(text: str, index: int) -> int:
    """把文本偏移转换为 1 开始的行号。

    参数:
        - `text`：完整配置文本，类型为 `str`。
        - `index`：目标字符偏移，类型为 `int`；负数按 0 处理。

    Returns:
        该偏移所在行号；偏移越界时返回 1，保证审计引用始终是合法行号。
    """
    if index < 0:
        return 1
    return text.count("\n", 0, min(index, len(text))) + 1


def _strip_php_comments(text: str) -> str:
    """删除 PHP 注释并保留换行，供结构化字段解析使用。

    参数:
        - `text`：配置文件完整文本，类型为 `str`。

    Returns:
        注释内容被替换为空格（块注释）或删除（行注释）的文本。所有换行原样
        保留，因此行号仍与原始配置一致；字符串字面量内的 `//`、`#` 不会被
        误删。结构化解析必须使用该结果，避免把注释掉的示例奖励或期次当成
        当前配置。
    """
    result: List[str] = []
    index = 0
    quote = ""
    escaped = False
    in_block = False
    while index < len(text):
        char = text[index]
        if in_block:
            if char == "*" and index + 1 < len(text) and text[index + 1] == "/":
                in_block = False
                index += 2
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if quote:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            in_block = True
            index += 2
            continue
        if (char == "/" and index + 1 < len(text) and text[index + 1] == "/") or char == "#":
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _bracket_end(text: str, start: int) -> int:
    """返回与指定左方括号配对的右方括号下标。

    参数:
        - `text`：待扫描文本，类型为 `str`。
        - `start`：左方括号 `[` 的下标，类型为 `int`；越界时返回 -1。

    Returns:
        配对 `]` 的下标；`start` 不是 `[` 或括号不配对时返回 -1。只做字符级
        计数，不解析 PHP 表达式，也不执行任何配置代码。
    """
    if start < 0 or start >= len(text) or text[start] != "[":
        return -1
    depth = 0
    for position in range(start, len(text)):
        char = text[position]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return position
    return -1


def _top_level_array_block(text: str, key: str) -> Tuple[str, int]:
    """提取嵌套层级最浅的指定键数组，避免误取等级条目内的同名数组。

    参数:
        - `text`：完整配置文本，类型为 `str`。
        - `key`：配置数组键名，例如 `prizes`，类型为 `str`。

    Returns:
        `(数组片段, 起始下标)`。同名键在配置中多次出现时，选择左方括号之前
        方括号净深度最小的那一处，也就是最外层定义；深度相同取最早出现的一处。
        找不到时返回 `("", -1)`。
    """
    pattern = re.compile(rf"['\"]{re.escape(key)}['\"]\s*=>\s*\[")
    best = ("", -1)
    best_depth = 10 ** 9
    for match in pattern.finditer(text or ""):
        start = match.end() - 1
        # 注释和字符串里的方括号也会参与计数，但它们的相对深度不会改变
        # “哪一处更外层”的判断，因此不需要复杂的状态机。
        depth = text[:start].count("[") - text[:start].count("]")
        if depth > best_depth:
            continue
        end = _bracket_end(text, start)
        if end < 0:
            continue
        if depth < best_depth:
            best_depth = depth
            best = (text[start:end + 1], start)
    return best


def _array_items(block: str) -> List[Tuple[int, str]]:
    """把方括号数组按顶层子数组切分。

    参数:
        - `block`：以 `[` 开头、以配对 `]` 结尾的数组片段，类型为 `str`。

    Returns:
        `(子数组起始偏移, 子数组文本)` 列表。只返回深度为 2 的数组条目，因此
        标量列表（如 `[110,120]`）返回空列表，由调用方按裸数字另行解析。
    """
    items: List[Tuple[int, str]] = []
    depth = 0
    item_start = -1
    for index, char in enumerate(block):
        if char == "[":
            depth += 1
            if depth == 2 and item_start < 0:
                item_start = index
        elif char == "]":
            if depth == 2 and item_start >= 0:
                items.append((item_start, block[item_start:index + 1]))
                item_start = -1
            depth -= 1
            if depth <= 0:
                break
    return items


def _display_name(raw: str) -> str:
    """把等级或奖励名称规范化为可读文本。

    参数:
        - `raw`：配置中的名称字面值，类型为 `str`。

    Returns:
        含中文时复用 `_normalize_zh_alias` 去掉标点与占位符；纯英文名称保留
        原文本并压缩空白，长度 2 至 60 才返回；URL、图片路径、翻译键等返回
        空字符串，避免把技术字符串当成业务名称。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    for fragment in ("http", "://", ".png", ".jpg", ".jpeg", ".gif", "@", "lang_map"):
        if fragment in lowered:
            return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        return _normalize_zh_alias(text)
    text = re.sub(r"\s+", " ", text).strip(" []'\"")
    return text if 2 <= len(text) <= 60 else ""


def _level_name(item_text: str) -> str:
    """提取等级条目中的可读名称。

    参数:
        - `item_text`：单个等级条目的源码片段，类型为 `str`。

    Returns:
        优先返回 `name` 数组中的 `zh` 值，其次返回字符串形式的 `name`；
        没有名称或名称不可读时返回空字符串。
    """
    match = re.search(
        r"['\"]name['\"]\s*=>\s*\[[^\]]{0,200}?['\"]zh['\"]\s*=>\s*"
        r"['\"]([^'\"\n]{1,60})['\"]",
        item_text or "",
    )
    if not match:
        match = re.search(
            r"['\"]name['\"]\s*=>\s*['\"]([^'\"\n]{1,60})['\"]",
            item_text or "",
        )
    return _display_name(match.group(1)) if match else ""


def _threshold_value(item_text: str) -> Tuple[str, str]:
    """提取等级条目的门槛展示值和字段名。

    参数:
        - `item_text`：单个等级条目的源码片段，类型为 `str`。

    Returns:
        `(展示值, 命中字段名)`。`getByTestMode(测试, 正式)` 渲染为
        `测试 X / 正式 Y`；普通数字保留原字面值；没有门槛时返回 `("", "")`。
        只读取数字字面量，不计算表达式结果。
    """
    for key in LEVEL_THRESHOLD_KEYS:
        pair = re.search(
            rf"['\"]{key}['\"]\s*=>\s*getByTestMode\(\s*"
            r"(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)",
            item_text or "",
        )
        if pair:
            return f"测试 {pair.group(1)} / 正式 {pair.group(2)}", key
        single = re.search(
            rf"['\"]{key}['\"]\s*=>\s*(\d+(?:\.\d+)?)",
            item_text or "",
        )
        if single:
            return single.group(1), key
    return "", ""


def _level_prize_ids(item_text: str) -> Tuple[str, ...]:
    """提取等级条目引用的奖励 id。

    参数:
        - `item_text`：单个等级条目的源码片段，类型为 `str`。

    Returns:
        去重后的奖励 id 元组，最多 `MAX_LEVEL_PRIZE_REFS` 条。支持
        `[110,120]` 裸数字与 `[['id' => 10, 'sex' => 1]]` 引用对象；引用对象
        只读取 `id`，不会把 `sex` 等附加字段当成奖励编号。
    """
    ids: List[str] = []
    for key in LEVEL_PRIZE_KEYS:
        match = re.search(
            rf"['\"]{key}['\"]\s*=>\s*\[",
            item_text or "",
        )
        if not match:
            continue
        start = match.end() - 1
        end = _bracket_end(item_text, start)
        if end < 0:
            continue
        block = item_text[start:end + 1]
        items = _array_items(block)
        if not items:
            ids.extend(re.findall(r"\d+", block))
            continue
        for _, sub_item in items:
            reference = re.search(r"['\"]id['\"]\s*=>\s*(\d+)", sub_item)
            if reference:
                ids.append(reference.group(1))
                continue
            bare = re.fullmatch(r"\[\s*(\d+)\s*\]", sub_item.strip())
            if bare:
                ids.append(bare.group(1))
    return tuple(dict.fromkeys(ids))[:MAX_LEVEL_PRIZE_REFS]


def _parse_level_items(
    text: str,
    block: str,
    block_start: int,
    config_file: str,
) -> Tuple[ActivityLevel, ...]:
    """把等级数组解析为结构化等级表。

    参数:
        - `text`：配置文件完整文本，类型为 `str`；只用于换算行号。
        - `block`：等级数组片段，类型为 `str`，来自最外层 `levels`/`level` 键。
        - `block_start`：数组片段在 `text` 中的起始下标，类型为 `int`。
        - `config_file`：项目内相对路径，类型为 `str`；用于生成审计引用。

    Returns:
        等级记录元组，最多 `MAX_LEVELS` 条。没有序号时按声明顺序补 1..n；
        条目缺少可读门槛和名称时仍保留，是否输出由事实层决定。
    """
    levels: List[ActivityLevel] = []
    for sequence, (offset, item_text) in enumerate(_array_items(block), start=1):
        reference = re.search(r"['\"]id['\"]\s*=>\s*(\d+)", item_text)
        ordinal = int(reference.group(1)) if reference else sequence
        threshold, _field = _threshold_value(item_text)
        levels.append(
            ActivityLevel(
                ordinal=ordinal,
                threshold=threshold,
                name=_level_name(item_text),
                prize_ids=_level_prize_ids(item_text),
                source_ref=(
                    f"{config_file}:"
                    f"{_line_number(text, block_start + offset)}"
                ),
            )
        )
        if len(levels) >= MAX_LEVELS:
            break
    return tuple(levels)


def _prize_field(item_text: str, key: str) -> str:
    """读取奖励定义条目中的单个字段字面值。

    参数:
        - `item_text`：单个奖励定义条目的源码片段，类型为 `str`。
        - `key`：字段名，例如 `type`、`num`、`days`，类型为 `str`。

    Returns:
        字段的字符串或数字字面值；字段不存在时返回空字符串。名称字段由
        `_level_name`/`_display_name` 单独处理，避免多语言数组被截断。
    """
    match = re.search(
        rf"['\"]{re.escape(key)}['\"]\s*=>\s*['\"]?([^'\"\n,]{1,60})['\"]?",
        item_text or "",
    )
    return match.group(1).strip() if match else ""


def _parse_prizes(
    text: str,
    config_file: str,
) -> Tuple[ActivityPrize, ...]:
    """解析顶层奖励定义表。

    参数:
        - `text`：配置文件完整文本，类型为 `str`。
        - `config_file`：项目内相对路径，类型为 `str`；用于生成审计引用。

    Returns:
        奖励记录元组，最多 `MAX_PRIZES` 条。只解析最外层 `prizes` 数组，避免
        把等级条目内的 `prizes` 引用数组误当成奖励定义；没有名称的奖励仍然
        保留 id 和引用，由事实层跳过展示。
    """
    block, block_start = _top_level_array_block(text, "prizes")
    if not block:
        return ()
    prizes: List[ActivityPrize] = []
    for match in re.finditer(r"(\d+)\s*=>\s*\[", block):
        start = match.end() - 1
        end = _bracket_end(block, start)
        if end < 0:
            continue
        item_text = block[start:end + 1]
        prizes.append(
            ActivityPrize(
                prize_id=match.group(1),
                name=_level_name(item_text),
                type=_prize_field(item_text, "type"),
                num=_prize_field(item_text, "num"),
                days=_prize_field(item_text, "days"),
                source_ref=(
                    f"{config_file}:"
                    f"{_line_number(text, block_start + match.start())}"
                ),
            )
        )
        if len(prizes) >= MAX_PRIZES:
            break
    return tuple(prizes)


def _literal_with_line(
    text: str,
    key: str,
) -> Tuple[str, str]:
    """读取配置顶层标量字面值及其行号。

    参数:
        - `text`：配置文件完整文本，类型为 `str`。
        - `key`：标量键名，例如 `vol`、`year`，类型为 `str`。

    Returns:
        `(字面值, 文件内行号)`。只接受数字或字符串数字；跳过注释行中的同名
        键，避免把 `// 'vol' => 1` 的示例值当成当前期次。找不到时返回
        `("", "")`。
    """
    for match in re.finditer(
        rf"['\"]{re.escape(key)}['\"]\s*=>\s*['\"]?(\d+)['\"]?",
        text or "",
    ):
        line_start = text.rfind("\n", 0, match.start()) + 1
        prefix = text[line_start:match.start()]
        # 注释行中的示例配置不参与取值：行内键名之前出现注释标记即跳过。
        if "//" in prefix or "#" in prefix or "/*" in prefix:
            continue
        return match.group(1), str(_line_number(text, match.start()))
    return "", ""


def _extract_alias_data(
    text: str,
    activity_id: str,
    config_file: str,
) -> Dict[str, object]:
    """从单个配置文件提取别名、时间窗和结构标记。

    参数:
        - `text`：配置文件完整文本，类型为 `str`。
        - `activity_id`：活动目录名或配置文件名干，类型为 `str`；用于生成英文
          目录词根。
        - `config_file`：项目内相对路径，类型为 `str`；只用于生成审计引用。

    Returns:
        包含 `aliases_zh`、`context_zh`、`aliases_en`、`level_names`、
        `level_count`、`level_key`、`level_field`、`stime`、`etime`、结构布尔
        标记和 `source_refs` 的字典。所有值只来自传入文本，不读取其他文件。
    """
    primary_zh: List[str] = []
    bracket_values = []
    for match in _BRACKET_ALIAS_PATTERN.finditer(text or ""):
        candidate = _normalize_zh_alias(match.group(1) or match.group(2) or "")
        if candidate:
            bracket_values.append(candidate)
            primary_zh.append(candidate)
    for match in _TITLE_PATTERN.finditer(text or ""):
        candidate = _normalize_zh_alias(match.group(1))
        if candidate:
            primary_zh.append(candidate)

    # 等级名称和 `name.zh` 都是面向用户的真实中文名，属于高置信别名。
    level_key = ""
    level_field = ""
    level_names: List[str] = []
    structured_levels: Tuple[ActivityLevel, ...] = ()
    level_block = ""
    level_block_start = -1
    # 结构化字段只解析未注释的配置：活动配置里常有注释掉的旧奖励或旧期次，
    # 直接扫描原文会把它们当成当前配置。
    structured_text = _strip_php_comments(text)
    for key in ("level", "levels"):
        block, block_start = _top_level_array_block(structured_text, key)
        if not block:
            continue
        names = [
            _normalize_zh_alias(match.group(1))
            for match in _LEVEL_NAME_PATTERN.finditer(block)
        ]
        names = [name for name in names if name]
        if names:
            level_names.extend(names)
        if key == "level" and ("'score'" in block or "'value'" in block):
            level_key, level_field = key, ("score" if "'score'" in block else "value")
            level_block, level_block_start = block, block_start
            break
        if key == "levels":
            if not level_key:
                level_key = "levels"
                level_field = "score" if "'score'" in block else "limit"
                level_block, level_block_start = block, block_start
    if level_key and level_block:
        structured_levels = _parse_level_items(
            structured_text,
            level_block,
            level_block_start,
            config_file,
        )
    if level_key and not level_names:
        level_names = []
    count_level_limits = len(re.findall(r"['\"]limit['\"]\s*=>", structured_text))
    count_level_constants = len(
        re.findall(r"::LEVEL_[A-Z0-9_]+\s*=>\s*\[", structured_text)
    )
    level_count = max(count_level_limits, count_level_constants)
    level_count = max(level_count, len(structured_levels))
    if len(level_names) > level_count:
        level_count = len(level_names)

    structured_prizes = _parse_prizes(structured_text, config_file)
    volume, vol_line = _literal_with_line(structured_text, "vol")
    year_value, year_line = _literal_with_line(structured_text, "year")
    vol_ref = f"{config_file}:{vol_line}" if vol_line else ""
    year_ref = f"{config_file}:{year_line}" if year_line else ""

    primary_zh.extend(level_names)
    unique_primary = tuple(dict.fromkeys(primary_zh))[:120]
    context_zh = tuple(
        dict.fromkeys(
            candidate
            for match in _ZH_PHRASE_PATTERN.finditer(text or "")
            if (candidate := _normalize_zh_alias(match.group(0)))
        )
    )[:MAX_CONTEXT_ZH]

    # 目录名拆出的英文词根与语言包英文标识一起参与链接；目录名本身最关键，
    # 但仍由问题中出现该词才得分，不做任何映射。
    directory_tokens = {
        token.casefold()
        for token in re.findall(
            r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+",
            activity_id or "",
        )
        if len(token) >= 4
    }
    # 英文别名只取目录词根和显式标题，不从整份配置正文抽取；否则普通字段名
    # 会让任意英文提问误命中多个活动。
    title_text = " ".join(match.group(1) for match in _TITLE_PATTERN.finditer(text or ""))
    english_tokens = set(directory_tokens)
    english_tokens |= _extract_english_tokens(title_text)

    def date_for(name: str) -> str:
        """读取指定时间键附近的日期字面量。

        参数:
            - `name`：`stime` 或 `etime`，类型为 `str`。

        Returns:
            `YYYY-MM-DD HH:MM:SS` 形式的字面量；没有日期时返回空字符串。
            只接受字符串字面量，不执行 `DateTime` 表达式。
        """
        match = re.search(
            rf"['\"]{name}['\"]\s*=>\s*[^\n]{{0,120}}?"
            r"(?:modify|getTimestamp)\s*\(\s*['\"]"
            r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)['\"]",
            text or "",
        )
        if not match:
            match = re.search(
                rf"['\"]{name}['\"]\s*=>[\s\S]{{0,160}}?"
                r"['\"](\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)['\"]",
                text or "",
            )
        return match.group(1) if match else ""

    refs: List[str] = []
    for needle in ("'stime'", "'level'", "'levels'", "'rank_prizes'"):
        index = (text or "").find(needle)
        if index >= 0:
            refs.append(f"{config_file}:{_line_number(text or '', index)}")
    return {
        "aliases_zh": unique_primary,
        "context_zh": context_zh,
        "aliases_en": tuple(sorted(english_tokens)),
        "level_names": tuple(dict.fromkeys(level_names))[:60],
        "level_count": level_count,
        "level_key": level_key,
        "level_field": level_field,
        "stime": date_for("stime"),
        "etime": date_for("etime"),
        "has_rank_prizes": bool(
            re.search(r"['\"](?:rank_prizes|top_prizes)['\"]\s*=>\s*\[", text or "")
        ),
        "has_allow_types": "'allow_types'" in (text or ""),
        "has_surprise": bool(
            re.search(r"['\"]surprise['\"]\s*=>\s*\[", text or "")
        ),
        "has_jump_switch": "'is_can_jump_x'" in (text or ""),
        "env_pair_count": len(re.findall(r"getByTestMode\s*\(", text or "")),
        "volume": volume,
        "year": year_value,
        "vol_ref": vol_ref,
        "year_ref": year_ref,
        "levels": structured_levels,
        "prizes": structured_prizes,
        "source_refs": tuple(refs[:MAX_SOURCE_REFS]),
    }


def _iter_marker_dirs(base_path: str) -> Iterable[Tuple[str, str]]:
    """枚举活动与全局配置目录，返回 `(marker, 绝对目录)` 序列。

    参数:
        - `base_path`：项目仓库绝对根目录，类型为 `str`。

    Returns:
        项目根、一级子项目及 `php/` 子目录下所有活动/配置标记目录。只返回真实
        存在的目录；目录枚举失败时跳过该前缀，不能中断构建。
    """
    prefixes = [""]
    try:
        prefixes.extend(
            name
            for name in sorted(os.listdir(base_path))
            if os.path.isdir(os.path.join(base_path, name))
            and name.casefold() not in SKIP_DIR_NAMES
        )
    except OSError:
        return
    seen: set = set()
    for prefix in prefixes:
        for marker in (*ACTIVITY_MARKERS, *CONFIG_MARKERS):
            for relative in (marker, f"php/{marker}"):
                candidate = os.path.join(base_path, *([prefix] if prefix else []), *relative.split("/"))
                if candidate in seen or not os.path.isdir(candidate):
                    continue
                seen.add(candidate)
                yield marker, candidate


def _extract_lang_package(
    config_file: str,
    read_text: Callable[[str], str],
) -> str:
    """读取全局配置对应的中文语言包文本。

    参数:
        - `config_file`：全局配置相对路径，例如 `waky3_miyouphp/php/config/noble.php`。
        - `read_text`：项目内只读文本读取函数，类型为 `Callable[[str], str]`。

    Returns:
        同路径 `php/lang/zh/<文件名>` 的文本；不是全局配置或文件不存在时返回
        空字符串。语言包只用于补充别名，不参与数值事实。
    """
    match = re.search(r"/(?:config|conf)/([^/]+)\.php$", config_file or "")
    if not match:
        return ""
    lang_file = re.sub(
        r"/(?:config|conf)/[^/]+\.php$",
        f"/lang/zh/{match.group(1)}.php",
        config_file,
    )
    if lang_file == config_file:
        return ""
    try:
        return read_text(lang_file)
    except (OSError, ValueError):
        return ""


def _backend_registration(
    marker: str,
    activity_id: str,
    config_file: str,
) -> Tuple[bool, str]:
    """判定活动配置是否落在后台活动登记路径中。

    参数:
        - `marker`：活动来源目录标记，类型为 `str`；`crontab`/`Logic` 等。
        - `activity_id`：活动目录名或配置文件名干，类型为 `str`。
        - `config_file`：项目内配置相对路径，类型为 `str`。

    Returns:
        `(是否登记, 后台读取配置路径)`。后台（`activityBackend/index.php`）的
        真实规则是优先 `crontab/<name>/config.php`，其次
        `Logic/<name>/boot/config.php`；只有命中这两条路径才算登记，全局配置
        和 `action_activity` 返回 `(False, "")`。
    """
    folded = (config_file or "").replace("\\", "/").casefold()
    if not folded or not activity_id:
        return False, ""
    crontab_path = f"/crontab/{activity_id}/config.php".casefold()
    logic_path = f"/logic/{activity_id}/boot/config.php".casefold()
    if marker == "crontab" and folded.endswith(crontab_path):
        return True, config_file
    if marker == "Logic" and folded.endswith(logic_path):
        return True, config_file
    return False, ""


def build_activity_knowledge(
    root: str,
    project: str,
    *,
    read_text: Optional[Callable[[str], str]] = None,
    max_activities: int = MAX_ACTIVITIES,
) -> Tuple[ActivityRecord, ...]:
    """从真实仓库构建活动知识库。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `read_text`：可注入的项目内只读文本读取函数，类型为
          `Optional[Callable[[str], str]]`；单测用构造内容替换真实仓库读取。
        - `max_activities`：最多构建的记录数，类型为 `int`；用于限制全仓扫描
          成本。

    Returns:
        去重后的活动记录元组。活动目录必须有 `config.php`，`Logic` 活动还接受
        `boot/config.php`；全局配置按 `php/config`、`php/conf` 下的 `.php` 文件
        建立。同一活动名只保留后台优先级最高的一条记录。读取失败的文件跳过，
        不影响其他活动；没有任何可读文件时返回空元组。
    """
    reader = read_text or _default_read_text(root, project)
    base_path = os.path.join(root, project)
    if not os.path.isdir(base_path):
        return ()
    records: List[ActivityRecord] = []
    seen: set = set()
    # 活动名 → 已收录记录的 marker 优先级。后台先查 crontab 配置，知识库必须
    # 与后台一致，避免 Logic boot 副本顶掉 crontab 记录或造成重复链接得分。
    activity_priority: Dict[str, int] = {}
    # 活动名 → 记录下标，用于按后台优先级原地替换同名记录。
    activity_index: Dict[str, int] = {}
    for marker, marker_path in _iter_marker_dirs(base_path):
        try:
            entries = sorted(os.listdir(marker_path))
        except OSError:
            continue
        for entry in entries:
            full_path = os.path.join(marker_path, entry)
            if marker in ACTIVITY_MARKERS:
                if not os.path.isdir(full_path):
                    continue
                # 后台活动后台先找 `crontab/<name>/config.php`，再找
                # `Logic/<name>/boot/config.php`；知识库按同一布局枚举。
                config_names = (
                    ("boot/config.php", "config.php")
                    if marker == "Logic"
                    else ("config.php",)
                )
                config_path = next(
                    (
                        os.path.join(full_path, *name.split("/"))
                        for name in config_names
                        if os.path.isfile(os.path.join(full_path, *name.split("/")))
                    ),
                    "",
                )
                if not config_path:
                    continue
                activity_id = entry
                relative = os.path.relpath(config_path, base_path).replace("\\", "/")
            elif marker in CONFIG_MARKERS:
                if not entry.endswith(".php") or not os.path.isfile(full_path):
                    continue
                activity_id = entry[:-4]
                relative = os.path.relpath(full_path, base_path).replace("\\", "/")
            else:
                continue
            normalized_relative = relative.replace("\\", "/")
            if not normalized_relative or normalized_relative.casefold() in seen:
                continue
            priority = BACKEND_MARKER_PRIORITY.get(marker, 9)
            existing_priority = activity_priority.get(activity_id)
            if existing_priority is not None and priority >= existing_priority:
                continue
            try:
                text = reader(normalized_relative)
            except (OSError, ValueError):
                continue
            if not text:
                continue
            seen.add(normalized_relative.casefold())
            data = _extract_alias_data(text, activity_id, normalized_relative)
            # 只保留有业务信号的文件：没有任何别名、等级表、时间窗或结构标记的
            # 配置文件无法被链接，继续保存只会占用记录上限并增加噪声。
            if not (
                data["aliases_zh"]
                or data["aliases_en"]
                or int(data["level_count"]) > 0
                or data["stime"]
                or data["has_rank_prizes"]
                or data["has_allow_types"]
            ):
                continue
            # 全局配置补充同名中文语言包：`noble.php` 的 `VIP 1` 来自语言包，
            # 不写死在代码里。
            lang_text = _extract_lang_package(normalized_relative, reader)
            if lang_text:
                lang_zh = []
                lang_values = []
                for match in re.finditer(
                    r"=>\s*['\"]([^'\"\n]{1,60})['\"]",
                    lang_text,
                ):
                    lang_values.append(match.group(1))
                    candidate = _normalize_zh_alias(match.group(1))
                    if candidate:
                        lang_zh.append(candidate)
                data["aliases_zh"] = tuple(
                    dict.fromkeys([*data["aliases_zh"], *lang_zh])
                )[:160]
                # 英文别名只取语言包值中的大写缩写（例如 `VIP 1` 的 `VIP`），
                # 不取键名或普通英文词；否则 `horn_level`、`last_noble` 这类
                # 文案词会让任意英文提问误命中多个活动。
                uppercase_tokens = {
                    raw.casefold()
                    for value in lang_values
                    for raw in re.findall(r"[A-Z]{2,12}", value)
                    if len(raw) >= 3
                }
                data["aliases_en"] = tuple(
                    dict.fromkeys([*data["aliases_en"], *uppercase_tokens])
                )[:120]
            backend_registered, backend_config_file = _backend_registration(
                marker,
                activity_id,
                normalized_relative,
            )
            record = ActivityRecord(
                activity_id=activity_id,
                marker=marker,
                config_file=normalized_relative,
                aliases_zh=tuple(data["aliases_zh"]),
                context_zh=tuple(data["context_zh"]),
                aliases_en=tuple(data["aliases_en"]),
                level_names=tuple(data["level_names"]),
                level_count=int(data["level_count"]),
                level_key=str(data["level_key"]),
                level_field=str(data["level_field"]),
                stime=str(data["stime"]),
                etime=str(data["etime"]),
                has_rank_prizes=bool(data["has_rank_prizes"]),
                has_allow_types=bool(data["has_allow_types"]),
                has_surprise=bool(data["has_surprise"]),
                has_jump_switch=bool(data["has_jump_switch"]),
                env_pair_count=int(data["env_pair_count"]),
                volume=str(data["volume"]),
                year=str(data["year"]),
                vol_ref=str(data["vol_ref"]),
                year_ref=str(data["year_ref"]),
                levels=tuple(data["levels"]),
                prizes=tuple(data["prizes"]),
                backend_registered=backend_registered,
                backend_config_file=backend_config_file,
                source_refs=tuple(data["source_refs"]),
            )
            existing_index = activity_index.get(activity_id)
            if existing_index is not None:
                # 后台优先级更高的配置替换先前记录：例如 Logic boot 配置先
                # 被扫描到时，随后出现的 crontab 配置才是后台真正读取的版本。
                records[existing_index] = record
            else:
                activity_index[activity_id] = len(records)
                records.append(record)
            activity_priority[activity_id] = priority
            if len(records) >= max_activities:
                return tuple(records)
    return tuple(records)


def load_activity_knowledge(
    root: str,
    project: str,
    fingerprint: str = "",
    *,
    read_text: Optional[Callable[[str], str]] = None,
) -> Tuple[ActivityRecord, ...]:
    """按代码指纹读取或重建活动知识库。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：项目标识，类型为 `str`。
        - `fingerprint`：当前项目代码快照指纹，类型为 `str`；为空时强制重建，
          避免在索引尚未完成时复用陈旧记录。
        - `read_text`：可注入的只读文本读取函数，类型为
          `Optional[Callable[[str], str]]`；测试使用。

    Returns:
        活动记录元组。指纹一致时直接复用进程缓存；否则重建并更新缓存。
        构建失败返回空元组，由调用方按“知识库不可用”降级。
    """
    cache_key = (root, project)
    if fingerprint:
        cached = _KNOWLEDGE_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
    try:
        records = build_activity_knowledge(
            root,
            project,
            read_text=read_text,
        )
    except Exception:
        return ()
    if fingerprint:
        _KNOWLEDGE_CACHE[cache_key] = (fingerprint, records)
    return records


def _question_zh_grams(question: str) -> Dict[str, int]:
    """生成问题中的中文片段及其长度。

    参数:
        - `question`：用户原始问题，类型为 `str`。

    Returns:
        `片段 → 长度` 映射，片段长度为 2 至 8 个汉字。用于与活动别名做包含
        匹配；停用词片段不会进入映射，避免所有活动因“活动/等级”等词得分。
    """
    grams: Dict[str, int] = {}
    for run in _ZH_PHRASE_PATTERN.findall(question or ""):
        limit = min(8, len(run))
        for size in range(2, limit + 1):
            for start in range(0, len(run) - size + 1):
                value = run[start:start + size]
                # 含通用提问词的片段一律不参与匹配：“名奖励”“的等级”这类
                # 组合会让无关活动靠问句结构得分。
                if any(term in value for term in GENERIC_ZH_TERMS):
                    continue
                grams[value] = size
    return grams


def _best_gram(grams: Dict[str, int], aliases: Sequence[str]) -> Tuple[int, str]:
    """计算问题片段与一组别名的最大包含长度。

    参数:
        - `grams`：问题中文片段映射，类型为 `Dict[str, int]`。
        - `aliases`：活动别名序列，类型为 `Sequence[str]`。

    Returns:
        `(最长命中长度, 命中片段)`；没有命中时返回 `(0, "")`。只做字符串包含，
        不使用编辑距离或同义词，保证链接可解释。
    """
    best_length = 0
    best_value = ""
    for alias in aliases:
        if not alias:
            continue
        for gram, length in grams.items():
            if length <= best_length:
                continue
            if gram in alias:
                best_length, best_value = length, gram
    return best_length, best_value


def _intent_flags(question: str) -> Dict[str, bool]:
    """识别问题中的业务意图，供活动结构对齐使用。

    参数:
        - `question`：用户原始问题，类型为 `str`。

    Returns:
        包含 `level`、`environment`、`time`、`rank`、`type`、`surprise`、
        `jump` 的布尔字典。意图只用于选择活动，不能替代源码事实。
    """
    folded = (question or "").casefold()
    return {
        "level": any(
            token in folded
            for token in ("等级", "档位", "档", "级别", "阶段", "几级", "vip")
        ),
        "environment": any(
            token in folded for token in ("测试", "正式", "生产", "环境")
        ),
        "time": any(
            token in folded
            for token in ("开始", "结束", "起止", "时间", "日期", "截止", "开放")
        ),
        "rank": bool(
            re.search(
                r"前\s*[一二三四五六七八九十\d]+\s*名|第\s*[一二三四五六七八九十\d]+\s*名",
                question or "",
            )
            or any(token in folded for token in ("名次", "排名", "日榜", "周榜", "月榜"))
        ),
        "type": any(token in folded for token in ("类型", "监听", "发奖")),
        "surprise": any(token in folded for token in ("惊喜", "区间", "保底")),
        "jump": "跳转" in folded,
    }


def _find_activity_record(
    records: Sequence[ActivityRecord],
    activity_id: str,
) -> Optional[ActivityRecord]:
    """按活动目录名在知识库中查找唯一记录。

    参数:
        - `records`：当前项目活动知识库记录，类型为 `Sequence[ActivityRecord]`。
        - `activity_id`：人工别名登记的目标活动目录名，类型为 `str`。

    Returns:
        目录名完全匹配的记录；大小写不一致时按折叠比较再查一次。为空或不存在
        时返回 `None`，调用方必须忽略该别名，不能换成其他活动。
    """
    target = str(activity_id or "").strip()
    if not target:
        return None
    for record in records:
        if record.activity_id == target:
            return record
    folded = target.casefold()
    for record in records:
        if (record.activity_id or "").casefold() == folded:
            return record
    return None


def _approved_alias_match(
    question: str,
    approved_aliases: Mapping[str, str],
) -> Optional[Tuple[str, str, bool]]:
    """在问题文本中匹配最长的人工登记别名。

    参数:
        - `question`：用户原始问题，类型为 `str`；匹配前会去掉空白，避免
          “鉴定 大师”这类排版差异导致漏命中。
        - `approved_aliases`：已启用的人工别名映射 `别名 → 活动目录`，类型为
          `Mapping[str, str]`；只包含数据库 `approved` 记录。

    Returns:
        `(命中的最长别名, 目标活动目录, 是否冲突)`。没有任何别名命中时返回
        `None`；多个同长度别名指向不同活动时 `是否冲突` 为 `True`，调用方必须
        按歧义链接处理。
    """
    compact = re.sub(r"\s+", "", question or "")
    if not compact or not approved_aliases:
        return None
    matches: List[Tuple[int, str, str]] = []
    for alias, activity_id in approved_aliases.items():
        cleaned = str(alias or "").strip()
        if len(cleaned) < 2 or cleaned not in compact:
            continue
        matches.append((len(cleaned), cleaned, str(activity_id or "")))
    if not matches:
        return None
    longest = max(item[0] for item in matches)
    best = sorted(
        (item for item in matches if item[0] == longest),
        key=lambda item: (item[2], item[1]),
    )
    alias = best[0][1]
    activity_id = best[0][2]
    ambiguous = len({item[2] for item in best}) > 1
    return alias, activity_id, ambiguous


def link_activity(
    question: str,
    records: Sequence[ActivityRecord],
    *,
    min_score: int = 12,
    min_margin: float = 1.3,
    approved_aliases: Optional[Mapping[str, str]] = None,
) -> Optional[ActivityLink]:
    """把用户问题链接到唯一活动记录。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。
        - `records`：活动知识库记录，类型为 `Sequence[ActivityRecord]`。
        - `min_score`：可接受的最低链接得分，类型为 `int`；低于该值返回 `None`。
        - `min_margin`：最高分与第二名的倍率门槛，类型为 `float`；用于拒绝
          分差不足的歧义链接。
        - `approved_aliases`：人工登记并启用的别名映射，类型为
          `Optional[Mapping[str, str]]`；问题命中时优先作为确定性链接输入，
          目标活动必须存在于 `records`，否则忽略该别名并回退启发式打分。

    Returns:
        最高分记录及其解释项。命中已启用人工别名时直接返回映射活动，
        `feedback_alias` 记录命中的别名；最高分不足 `min_score` 时返回 `None`；
        分差不足或同长度别名冲突时返回 `ambiguous=True` 的结果，调用方必须按
        “未链接”处理，不能据此读取该活动配置。
    """
    if not records:
        return None
    if approved_aliases:
        match = _approved_alias_match(question, approved_aliases)
        if match is not None:
            alias, activity_id, ambiguous = match
            record = _find_activity_record(records, activity_id)
            if record is not None:
                # 人工登记别名是显式业务知识，分数只用于审计展示，不再参与
                # 与启发式候选的分差比较；同长度冲突时仍按歧义返回。
                return ActivityLink(
                    record=record,
                    score=200 + len(alias) * 8,
                    matched_terms=(alias,),
                    ambiguous=ambiguous,
                    feedback_alias=alias,
                )
            # 映射目标已从知识库消失（目录改名或删除）：忽略该别名，继续按
            # 仓库真实文案做启发式链接，避免使用过期映射。
    folded_question = (question or "").casefold()
    grams = _question_zh_grams(question)
    flags = _intent_flags(question)
    scored: List[Tuple[int, ActivityRecord, Tuple[str, ...]]] = []
    for record in records:
        score = 0
        matched: List[str] = []
        directory = (record.activity_id or "").casefold()
        if len(directory) >= 4 and directory in folded_question:
            score += 120
            matched.append(record.activity_id)
        primary_length, primary_value = _best_gram(grams, record.aliases_zh)
        if primary_length >= 3:
            score += primary_length * 6
            # 完整命中活动展示名（而不是长文案中的一个三字片段）说明问题直接
            # 点名该活动，额外加权以高于“只是提到同一个词”的活动。
            if primary_value in record.aliases_zh:
                score += 15
            matched.append(primary_value)
        context_length, context_value = _best_gram(grams, record.context_zh)
        if context_length >= 4 and context_length > primary_length:
            score += context_length * 2
            matched.append(context_value)
        level_length, level_value = _best_gram(grams, record.level_names)
        if level_length >= 2:
            score += 40
            matched.append(level_value)
        for token in record.aliases_en:
            if len(token) >= 3 and token in folded_question:
                score += 25
                matched.append(token)
        if flags["level"] and record.level_count > 0:
            score += 20
        if flags["environment"] and record.env_pair_count > 0:
            score += 25
        if flags["time"] and record.stime:
            score += 30
        if flags["rank"] and record.has_rank_prizes:
            score += 30
        if flags["type"] and record.has_allow_types:
            score += 25
        if flags["surprise"] and record.has_surprise:
            score += 25
        if flags["jump"] and record.has_jump_switch:
            score += 25
        if score > 0:
            scored.append((score, record, tuple(dict.fromkeys(matched))))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].activity_id))
    top = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if top[0] < min_score:
        return None
    if second_score > 0 and top[0] < second_score * min_margin:
        return ActivityLink(
            record=top[1],
            score=top[0],
            matched_terms=top[2],
            ambiguous=True,
        )
    return ActivityLink(record=top[1], score=top[0], matched_terms=top[2])


def _path_priority(
    relative_path: str,
    tokens: Sequence[str],
    global_config: bool,
    level_field: str = "",
) -> Tuple[int, int, int, str]:
    """计算相关实现文件的排序键。

    参数:
        - `relative_path`：项目内相对路径，类型为 `str`。
        - `tokens`：活动名或配置名干，类型为 `Sequence[str]`。
        - `global_config`：是否为全局配置分组，类型为 `bool`；全局配置按
          服务/控制器/模型排序，活动目录按同目录入口优先。
        - `level_field`：等级门槛字段（`score`/`limit`），类型为 `str`；文件名
          含该字段的实现在“按什么字段计算”问题中优先读取。

    Returns:
        `(领域优先, 角色优先, 路径)` 元组，数值越小越优先。排序只使用文件名和
        路径，不读取文件内容。
    """
    folded = relative_path.replace("\\", "/").casefold()
    basename = folded.rsplit("/", 1)[-1]
    domain = 0
    for token in tokens:
        if token and token in basename:
            domain = 1
            break
    field_bonus = 1 if level_field and level_field in basename else 0
    role = 9
    if global_config:
        if "service" in basename:
            role = 0
        elif "control" in basename:
            role = 1
        elif "model" in basename:
            role = 2
    else:
        if basename.endswith("boot.php"):
            role = 0
        elif "control" in basename:
            role = 1
        elif "service" in basename:
            role = 2
    return (-domain, -field_bonus, role, folded)


def select_related_files(
    record: ActivityRecord,
    known_files: Sequence[str],
    *,
    limit: int = 6,
) -> List[str]:
    """为链接到的活动选择同活动实现文件。

    参数:
        - `record`：已链接的活动记录，类型为 `ActivityRecord`。
        - `known_files`：项目符号索引中的真实文件路径，类型为
          `Sequence[str]`；只从中选择，不扫描仓库。
        - `limit`：最多返回的文件数，类型为 `int`。

    Returns:
        同活动服务、控制器或全局配置实现文件的相对路径列表。排除
        `vendor/lib/tests/phpunit` 和配置文件本身；没有候选时返回空列表。
    """
    if limit < 1:
        return []
    global_config = record.marker in CONFIG_MARKERS
    directory = record.config_file.rsplit("/", 1)[0] if "/" in record.config_file else ""
    tokens = [(record.activity_id or "").casefold()]
    candidates: List[str] = []
    for path in known_files:
        normalized = str(path or "").replace("\\", "/")
        folded = normalized.casefold()
        if not normalized or not folded.endswith(".php"):
            continue
        if any(
            f"/{part}/" in f"/{folded}/"
            for part in ("vendor", "lib", "tests", "phpunit")
        ):
            continue
        basename = folded.rsplit("/", 1)[-1]
        if global_config:
            # 全局配置只接受实现文件，不能把其他 `php/config/*.php` 也当实现。
            if "/config/" in f"/{folded}/" or "/conf/" in f"/{folded}/":
                continue
            if not any(
                token and token in basename
                for token in tokens
            ):
                continue
        else:
            if folded.endswith("/config.php"):
                continue
            same_directory = bool(
                directory and folded.startswith(f"{directory.casefold()}/")
            )
            same_activity = bool(
                record.activity_id
                and record.activity_id.casefold() in basename
            )
            if not same_directory and not same_activity:
                continue
        candidates.append(normalized)
    ordered = sorted(
        dict.fromkeys(candidates),
        key=lambda path: _path_priority(
            path,
            tokens,
            global_config,
            record.level_field,
        ),
    )
    return ordered[:limit]
