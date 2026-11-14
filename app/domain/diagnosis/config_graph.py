"""PHP 配置证据的结构化图模型与通用枚举查询。

该模块把已经进入诊断证据窗口的 PHP 配置行解析成“文件 + 数组路径 + 键 + 字面值”
节点，并支持按等级/档位等重复数字键做通用枚举。它替代“每遇到一个新活动就写一个
专属分析器”的做法：新活动、新业务名只要配置结构能被证据窗口读到，就能直接回答。

边界与安全约束：
- 只读取 `EvidenceLine`，不访问仓库、数据库、Redis 或模型；
- 只保留数字、布尔、代码常量和少量面向用户的奖励标签，URL、路径、令牌、密钥和
  普通字符串一律丢弃，避免诊断报告扩大敏感信息暴露面；
- 不把配置注释中的数值当成可执行事实，注释只用于候选排序和结果解释。
"""

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Sequence, Tuple

from app.code_tools import extract_generic_anchors
from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.domain.diagnosis.analyzers.configuration import (
    _KEY_INTENT_ALIASES,
    _activity_root,
    _inline_comment,
    _lines_in_supported_config_dirs,
    _safe_literal_text,
    _scope_identifier,
    _scoped_config_lines,
    scoped_config_evidence,
)
from app.query_understanding import detect_semantic_concepts


# 字符串键数组：`'prize' => [`。
_STRING_GROUP_PATTERN = re.compile(
    r"^['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*\["
)
# 类常量键数组：`\Model\UserNobleModel::LEVEL_RANGER => [`。贵族等级表、配置
# 枚举表常用类常量当等级键，源码里没有数字键；若把这一层丢掉，12 个等级的
# value/score 会全部挤到同一个 `level` 路径下，既无法分级也无法回答门槛。
_CONSTANT_GROUP_PATTERN = re.compile(
    r"^(?P<key>\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*)\s*=>\s*"
    r"(?:\[|array\s*\()"
)
# 数字键数组：`1 => [` 或 `1 => array(`，用于等级、档位等重复块。
_NUMERIC_GROUP_PATTERN = re.compile(r"^(?P<key>\d+)\s*=>\s*(?:\[|array\s*\()")
# 数字键环境选择：`1 => getByTestMode(`，测试/正式分支由后续注释标记分隔。
_TEST_MODE_GROUP_PATTERN = re.compile(r"^(?P<key>\d+)\s*=>\s*getByTestMode\s*\(")
# 无键列表项：`[` 或 `[ // 注释` 单独成行，表示上一级数组中的一个匿名元素，
# 例如 `'levels' => [ ['id' => 1, ...], ... ]`。这类结构在活动配置里非常常见，
# 但没有任何数字键，旧解析器会把内部键值直接挂到 `levels` 下而无法分级。
_LIST_ITEM_PATTERN = re.compile(r"^\[\s*(?://.*)?$")
# 行内嵌套数组：`['id' => 1, ... "name" => [`。数组开放符与兄弟键值写在同一行，
# 需要按缩进补一个虚拟层级，否则 `name.zh` 等子字段会并入错误路径。
_INLINE_GROUP_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*(?:\[|array\s*\()"
)
# 单行内可能同时出现多个键值对，使用 finditer 逐个提取。
_LITERAL_ENTRY_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*"
    r"(?P<value>-?\d+(?:\.\d+)?|true|false|'[^'\n]{1,80}'|\"[^\"\n]{1,80}\")(?=\s*(?:[,}\]]|//|$))",
    re.IGNORECASE,
)
# 单行数字/字符串标量列表：`'prizes' => [110,120]`。仅当方括号内没有嵌套数组
# 或键值对时才当作标量列表，避免把 `[['id' => 200]]` 误解析成两个数字。
_INLINE_SCALAR_LIST_PATTERN = re.compile(
    r"(?P<quote>['\"])?(?P<key>[A-Za-z_0-9][A-Za-z0-9_.:-]*)(?(quote)['\"])\s*=>\s*"
    r"\[(?P<body>[^\[\]=>]*)\]\s*,?"
)
# 列表中的单个标量元素：`70002, //注释` 这种一行一个值、可带行尾注释的写法。
_SCALAR_ITEM_PATTERN = re.compile(
    r"^(?P<value>-?\d+(?:\.\d+)?|'[^'\n]{1,80}'|\"[^\"\n]{1,80}\")\s*,?\s*(?://.*)?$"
)
# 表达式日期：`$_dateTime->modify('2026-09-23 12:00:00')` 或
# `getTimestamp('2026-05-09 12:00:00', ...)`。活动起止时间在 PHP 里几乎都以
# 这种方式表达，旧解析器只接受数字字面量，导致 stime/etime 永远答不出来。
_DATE_LITERAL_PATTERN = re.compile(
    r"['\"](\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)['\"]"
)
# 单行赋值键：用于把表达式日期与 `'stime' => ...` 这样的键配对。
_ASSIGNMENT_KEY_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>"
)
# 计数提问词：问题要求“多少/几个/哪些”时，除了逐项列表还要给出总数量。
_COUNT_QUESTION_WORDS = (
    "多少", "几", "数量", "哪些", "哪几", "共", "总共有", "一共",
)
# 中文数字映射，用于“六个等级”“前 10 名”这类数量约束。
_CHINESE_NUMERALS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_CONSTANT_ENTRY_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*"
    r"(?P<value>\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*)"
    # `\Lang\Lang::get('ranger', 'noble')` 是方法调用而不是配置字面量；只有常量
    # 后面紧跟逗号、右括号或行尾时才接受，避免把方法名截成“常量”。
    r"(?=\s*(?:[,}\]]|//|$))"
)
# 单行环境覆盖值：`'merchantId' => getByTestMode('', ' alvV...'),`。
# 多行地址类配置同样会被识别，但地址值在渲染阶段统一脱敏。
_ENV_VALUE_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*"
    r"getByTestMode\(\s*"
    r"(?P<test>'[^'\n]*'|\"[^\"\n]*\"|-?\d+(?:\.\d+)?|true|false)\s*,\s*"
    r"(?P<prod>'[^'\n]*'|\"[^\"\n]*\"|-?\d+(?:\.\d+)?|true|false)\s*\)"
)
# 命中这些字段时不展示任何候选值，只说明“已配置”，避免把密钥、令牌或签名
# 材料写进诊断报告。
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:secret|token|password|passwd|private|credential|sign|accesskey|publickey)",
    re.IGNORECASE,
)
# 只有普通业务标识符允许原样展示；URL、路径、邮箱和含空格文本统一脱敏。
_SAFE_ENV_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
# 环境分支标记：测试分支在前，`//正式配置` 之后才是正式配置。
_PRODUCTION_BRANCH_MARKER = "//正式配置"
_TEST_BRANCH = "test"
_PRODUCTION_BRANCH = "production"
# 单个等级最多渲染的键值数量，避免异常配置把一条事实撑成整页源码。
_MAX_ENTRIES_PER_LEVEL = 24
# 通用枚举最多返回的等级数量，防止超大配置表占满报告。
_MAX_ENUMERATED_LEVELS = 60
_ENUMERATION_WORDS = (
    "每个",
    "每一",
    "所有",
    "全部",
    "列出",
    "分别",
    "各等级",
    "各档",
    "逐级",
    "各档位",
)
# 报告展示时隐藏的技术字段：内部编号、资源 id、每日步进、图片路径和跳转
# 开关都属于实现细节。它们留在证据源码里可审计，但不应占据结论正文。
_HIDDEN_ENTRY_KEYS = {
    "id", "gid", "tpid", "step", "is_daily", "is_can_jump_x", "can_jump_x",
    "jump_x", "is_can_jump", "can_jump", "image", "img", "icon", "pic",
    "url", "link", "path", "zip", "sort", "order", "index", "weight",
    "exp_card_img", "tip_image", "give_tip_image", "ext", "extend", "extra",
}
# 多语言名称的键：`name` 数组下会并列 `zh/en/in` 等分支。逐条输出会变成
# “en=...，in=...，zh=...”，只保留一个中文或首个名称即可。
_NAME_GROUP_KEYS = {"name", "title", "desc", "label", "text", "nick", "nickname"}
_LANGUAGE_KEYS = {
    "zh", "en", "in", "hi", "vi", "tr", "pt", "es", "ur", "ar", "ms", "th",
    "id", "ja", "ko", "ru", "de", "fr", "tw", "hk",
}
# 奖励引用键：值通常是指向同文件奖品定义表的 id，需要解析成奖品名称。
_PRIZE_GROUP_KEYS = {"prize", "prizes", "reward", "rewards", "gift", "gifts"}
# 结论中优先展示的字段顺序：名称和门槛在最前，奖励其次，其他键按源码顺序。
_ENTRY_PRIORITY = {
    "score": 1, "threshold": 2, "limit": 2, "target": 3, "need": 3,
    "value": 4, "price": 5, "rate": 5, "pro": 5, "scale": 5, "stock": 5,
    "days": 6, "expire_day": 6, "num": 6,
    "stime": 8, "etime": 9,
}
# 配置键到中文展示标签的通用映射。未命中的键仍以源码键名展示，避免为了
# 新活动不断追加映射；这些标签只影响可读性，不改变数值来源。
_ENTRY_LABELS = {
    "score": "积分门槛",
    "threshold": "门槛",
    "limit": "门槛上限",
    "target": "目标值",
    "need": "需要数量",
    "value": "等级值",
    "stime": "开始时间",
    "etime": "结束时间",
}
# 用户点名某一类取值时只保留对应字段，避免把价格、折扣、图片等无关配置
# 塞满结论。键名来自通用业务意图，不绑定具体活动。
_FOCUS_KEY_SETS = {
    "门槛": {"score", "threshold", "limit", "target", "need"},
    "奖励": {
        "prize", "prizes", "reward", "rewards", "gift", "gifts",
        "num", "days", "type", "id",
    },
    "价格": {"price", "cost"},
    "比例": {"rate", "pro", "scale", "percent"},
    "库存": {"stock", "inventory"},
    "时间": {"stime", "etime", "deadline", "pre_time"},
    "日期": {"stime", "etime", "deadline", "date"},
    # 燃料/投入规则/瓜分名次在不同活动里的键名不统一，按业务意图归类；
    # 与其它意图同时出现时不做裁剪，完整保留可见字段。
    "燃料": {"value", "need", "fuel_value"},
    "规则": {"scale", "rate", "multiple", "limit"},
    "瓜分": {"top_rank", "top_n", "rank", "top_prizes"},
}
# 奖励类字段集合，用于判断是否需要解析奖品引用。
_REWARD_FOCUS_KEYS = {
    "prize", "prizes", "reward", "rewards", "gift", "gifts",
    "num", "days", "type", "id",
}


@dataclass(frozen=True)
class ConfigEntry:
    """配置数组中一个带完整路径和行号的字面量节点。

    属性说明：
    - `file`：项目内相对路径，用于把事实定位回真实配置文件；
    - `line`：1 开始的源码行号，与 `ref` 一起构成可审计引用；
    - `path`：从顶层数组到当前键的完整路径，例如
      `("prize", "1", "type")`；
    - `value_text`：已通过敏感值过滤的字面值，只包含数字、布尔、代码常量或
      面向用户的奖励标签；
    - `comment`：同一行末尾的 `//` 注释，用于候选排序和结果解释；
    - `content`：原始源码行，用于通用业务词重合度排序，不直接写入结论；
    - `branch`：环境分支，`test`、`production` 或空字符串。
    """

    file: str
    line: int
    path: Tuple[str, ...]
    value_text: str
    comment: str
    content: str
    branch: str = ""

    @property
    def ref(self) -> str:
        """返回报告和证据校验共用的 `文件:行号` 引用。"""
        return f"{self.file}:{self.line}"

    @property
    def key(self) -> str:
        """返回路径末级键名，例如 `num` 或 `gid`。"""
        return self.path[-1]

    @property
    def parent(self) -> Tuple[str, ...]:
        """返回去掉末级键后的数组路径。"""
        return self.path[:-1]


@dataclass(frozen=True)
class ConfigEnvValue:
    """记录一个 `getByTestMode(测试值, 正式值)` 环境覆盖配置。

    `ConfigEntry` 只保存字面量，本类额外保留测试和正式两个分支，用于回答
    “测试环境是什么、正式环境是什么”以及商户号、回调地址等字符串配置。
    渲染阶段会对敏感字段和地址值脱敏，避免扩大密钥或内网地址暴露面。
    """

    file: str
    line: int
    path: Tuple[str, ...]
    test_value: str
    production_value: str
    comment: str
    branch: str = ""

    @property
    def ref(self) -> str:
        """返回报告和证据校验共用的 `文件:行号` 引用。"""
        return f"{self.file}:{self.line}"

    @property
    def key(self) -> str:
        """返回配置键名，例如 `merchantId`。"""
        return self.path[-1] if self.path else ""


@dataclass
class _OpenGroup:
    """解析过程中尚未闭合的 PHP 数组层级。

    属性说明：
    - `indent`：该数组开放行折算后的缩进宽度；
    - `key`：数组在上一级中的键名；匿名列表元素使用递增序号作为虚拟键；
    - `test_mode`：是否由 `N => getByTestMode(` 打开，用于区分测试/正式分支；
    - `branch`：当前环境分支标记；
    - `comment`：数组开放行之前的连续中文注释，用于判断该组是否与用户问题相关；
    - `child_index`：已经打开的匿名列表元素数量，用于生成下一个虚拟序号；
    - `scalar_index`：已经收集的标量列表元素数量。
    """

    indent: int
    key: str
    test_mode: bool
    branch: str
    comment: str = ""
    child_index: int = 0
    scalar_index: int = 0


@dataclass(frozen=True)
class _EnumerationCandidate:
    """描述一个可枚举的重复数字键分组及其选择依据。"""

    file: str
    position: int
    prefix: Tuple[str, ...]
    levels: Tuple[int, ...]
    entries: Tuple[ConfigEntry, ...]
    score: int


def _line_indent(content: str) -> int:
    """计算源码行的缩进宽度，用于识别 PHP 数组层级。

    参数:
        - `content`：证据窗口中的一行原始源码，类型为 `str`。

    Returns:
        行首空格和制表符折算后的宽度。制表符按四个空格折算，便于不同风格仓库
        仍能稳定比较层级。
    """
    prefix = content[: len(content) - len(content.lstrip())]
    return len(prefix.replace("\t", "    "))


def _question_key_terms(question: str) -> set:
    """提取问题中直接写出的英文配置键（例如 `limit`、`top_rank`、`pro`）。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        小写英文标识符集合。长度至少三个字符，过滤常见英文虚词和技术缩写，
        避免把普通单词当成配置键；只用于候选排序，不会据此生成事实。
    """
    ignored = {
        "and", "the", "for", "with", "from", "this", "that", "api", "url",
        "php", "sql", "redis", "json", "http", "https",
    }
    terms = set()
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_.]{2,}", question or ""):
        value = raw.rsplit(".", 1)[-1].casefold()
        if len(value) < 3 or value in ignored:
            continue
        terms.add(value)
    return terms


def _question_count_hint(question: str) -> Optional[int]:
    """提取问题中明确给出的数量约束，例如“六个等级”“前 10 名”“3 轮”。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        一到六十之间的数量；没有明确数量时返回 `None`。该值只用于在多个
        配置数组之间选择条目数匹配的分组，不参与结论生成。
    """
    match = re.search(
        r"前\s*(\d+)\s*名|([一二三四五六七八九十\d]+)\s*(?:个|级|轮|支|种|名|阶段|档位|档|面|条|项)",
        question or "",
    )
    if not match:
        return None
    raw = match.group(1) or match.group(2) or ""
    if raw.isdigit():
        value = int(raw)
    else:
        value = _CHINESE_NUMERALS.get(raw)
    if value and 1 <= value <= 60:
        return value
    return None


def _question_ordinal(question: str) -> Optional[int]:
    """提取“第 3 个等级”这类一基序号。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        一到十之间的序号；没有明确序号时返回 `None`。序号只用于把逐项枚举
        收窄到用户点名的一个档位，避免回答全表。
    """
    # “VIP 等级 99”“等级 99”直接写明等级编号，编号不需要中文数字映射。
    # 这类问题必须按点名等级渲染；编号不存在时返回空结果，由上层报告缺口，
    # 不能退化成“列出全部等级”。
    explicit = re.search(r"(?:等级|级别|VIP)\s*(\d{1,2})", question or "", re.IGNORECASE)
    if explicit:
        value = int(explicit.group(1))
        # 保留到 999：不存在的编号也要作为序号返回，让渲染阶段得到空等级，
        # 再由上层报告缺口；直接返回 None 会退化成输出全部等级。
        return value if 1 <= value <= 999 else None
    match = re.search(
        r"第\s*([一二三四五六七八九十\d]+)\s*(?:个|档|级|名|项|轮)"
        # “2 级需要多少燃料”省略“第”字；只有紧跟等级词且后接取值语境时
        # 才作为序号，避免把“四个阶段”误读成第 4 档。
        r"|(\d+|[一二三四五六七八九十])\s*(?:级|档)"
        r"(?=\s*(?:需要|门槛|奖励|燃料|多少|是|的|给|对应|分|要))",
        question or "",
    )
    if not match:
        return None
    raw = match.group(1) or match.group(2) or ""
    if raw.isdigit():
        value = int(raw)
    else:
        value = _CHINESE_NUMERALS.get(raw)
    # 保留到 999：不存在的编号也要作为序号返回，让渲染阶段得到空等级，
    # 再由上层报告缺口；直接返回 None 会退化成输出整张等级表。
    return value if value and 1 <= value <= 999 else None


def _question_top_count(question: str) -> Optional[int]:
    """提取“前 3 名/前三名”这类名次数量约束。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        一到六十之间的名次数量；没有“前 N 名”写法时返回 `None`。用于把名次表
        收窄到用户点名的前几名，避免“前 3 名”回答整张 1-10 名奖励表。
    """
    match = re.search(r"前\s*(\d+|[一二三四五六七八九十])\s*名", question or "")
    if not match:
        return None
    raw = match.group(1)
    value = int(raw) if raw.isdigit() else _CHINESE_NUMERALS.get(raw)
    return value if value and 1 <= value <= 60 else None


def _question_rank_ordinal(question: str) -> Optional[int]:
    """提取“第 1 名/第 6 名”这类名次序号。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        一到十之间的名次序号；没有“第 N 名”写法时返回 `None`。名次序号只用于
        优先选择 `rank_prizes`/`top_prizes` 分组，避免与“第 N 档”共用加权规则。
    """
    match = re.search(
        r"第\s*([一二三四五六七八九十\d]+)\s*名",
        question or "",
    )
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        value = int(raw)
    else:
        value = _CHINESE_NUMERALS.get(raw)
    # 配置名次表可能超过十名（如 1-20 名），保留到 60 以便正确判定
    # “第 99 名”不存在；超出范围时由上层报告缺口。
    return value if value and 1 <= value <= 60 else None


def _safe_constant(value: str) -> str:
    """校验代码常量引用是否可以写入诊断事实。

    参数:
        - `value`：`Class::CONSTANT` 形式的候选常量文本，类型为 `str`。

    Returns:
        合法时返回原常量文本，否则返回空字符串。只允许类名和常量名，避免把
        含调用、字符串拼接或外部输入的表达式当作配置值。
    """
    if re.fullmatch(r"\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*", value):
        return value
    return ""


def _parse_file_entries(
    file_name: str,
    file_lines: Sequence[EvidenceLine],
) -> Tuple[
    List[ConfigEntry],
    Dict[Tuple[str, ...], int],
    Dict[Tuple[str, ...], str],
    Dict[int, Tuple[str, ...]],
]:
    """把单个配置文件证据解析为带路径的字面量节点、成员数量和组注释。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。
        - `file_lines`：同一文件按行号排序的证据行，类型为
          `Sequence[EvidenceLine]`；证据窗口可能不完整，缺失层级只影响该分支的
          枚举完整性，不影响已读到的配置事实。

    Returns:
        `(配置节点列表, 成员数量表, 组注释表, 行号路径表)`。配置节点按缩进维护
        数组栈生成；成员数量表以数组路径为键记录直接成员数量；组注释表记录
        数组开放行前的连续中文注释，用于把中文业务名对应到具体配置数组；
        行号路径表把每个源码行映射到它所属的数组路径，供 `getByTestMode`
        环境值补全等级前缀。
    """
    stack: List[_OpenGroup] = []
    entries: List[ConfigEntry] = []
    line_paths: Dict[int, Tuple[str, ...]] = {}
    seen = set()
    # 数组直接成员数量：路径 → 成员键集合。使用集合避免同一键重复计数。
    member_keys: Dict[Tuple[str, ...], set] = {}
    # 数组路径 → 开放行前连续中文注释，用于候选排序。
    group_comments: Dict[Tuple[str, ...], str] = {}
    # 最近出现的连续中文注释。数组开放前的注释通常说明该数组的业务含义，
    # 例如 `// 金币奖池`、`// 任务送礼阶段奖励`，是中文问题定位配置的关键信号。
    recent_comments: List[str] = []
    for item in sorted(file_lines, key=lambda row: row.line):
        content = item.content
        stripped = content.strip()
        if not stripped:
            recent_comments = []
            continue
        indent = _line_indent(content)
        # `], "is_can_jump_x" => 0, ...` 这类行先用 `]` 关闭内层数组，随后
        # 的键值仍属于同一匿名列表元素。解析前保存原路径，避免缩进闭合把
        # 兄弟键错误挂到上一层数组。
        close_then_siblings = stripped.startswith("]") and "=>" in stripped
        leading_closes = stripped.split("=>", 1)[0].count("]")
        keep_frames = (
            stack[: max(0, len(stack) - leading_closes)]
            if close_then_siblings
            else stack
        )
        path_before_close = tuple(entry.key for entry in keep_frames)
        branch_before_close = keep_frames[-1].branch if keep_frames else ""
        # 先按缩进闭合已结束的数组，注释行同样参与闭合，否则“//正式配置”这类
        # 分支标记会停留在上一层数组里，正式分支被误当成测试分支。
        while stack and indent <= stack[-1].indent:
            stack.pop()
        if stripped.startswith("//"):
            # 正式分支标记通常单独成行，也可能跟在数组开头。必须先切换分支，
            # 再解析同一行后面的键值，否则正式配置会被误当成测试配置。
            if _PRODUCTION_BRANCH_MARKER in content and stack and stack[-1].test_mode:
                stack[-1].branch = _PRODUCTION_BRANCH
            recent_comments.append(stripped.lstrip("/").strip())
            recent_comments = recent_comments[-3:]
            continue

        # 正式分支标记也可能出现在键值行里，在解析该行字面量之前切换分支。
        if _PRODUCTION_BRANCH_MARKER in content and stack and stack[-1].test_mode:
            stack[-1].branch = _PRODUCTION_BRANCH

        group_comment = " ".join(recent_comments)
        group = (
            _TEST_MODE_GROUP_PATTERN.match(stripped)
            or _NUMERIC_GROUP_PATTERN.match(stripped)
            or _STRING_GROUP_PATTERN.match(stripped)
        )
        # 常量键（`\Model\UserNobleModel::LEVEL_RANGER => [`）不是字符串键，
        # 也不带数字；单独识别后按出现顺序补虚拟序号，才能逐级枚举。
        constant_group = None if group else _CONSTANT_GROUP_PATTERN.match(stripped)
        if group:
            is_test_mode = bool(_TEST_MODE_GROUP_PATTERN.match(stripped))
            group_key = group.group("key")
            stack.append(
                _OpenGroup(
                    indent=indent,
                    key=group_key,
                    test_mode=is_test_mode,
                    branch=_TEST_BRANCH if is_test_mode else "",
                    comment=group_comment,
                )
            )
            if group_comment:
                group_comments.setdefault(
                    tuple(entry.key for entry in stack), group_comment
                )
        elif constant_group and stack:
            # 常量键数组最常见于贵族等级表：常量声明顺序就是等级 1、2、3，
            # 但源码本身没有数字键。沿用匿名列表的虚拟序号机制，为每个常量
            # 键分配递增序号，使后续 `_render_levels` 能按等级输出。
            parent = stack[-1]
            parent.child_index += 1
            stack.append(
                _OpenGroup(
                    indent=indent,
                    key=str(parent.child_index),
                    test_mode=False,
                    branch=parent.branch,
                    comment=group_comment,
                )
            )
            if group_comment:
                group_comments.setdefault(
                    tuple(entry.key for entry in stack), group_comment
                )
        elif stack and stripped.startswith("["):
            # 匿名列表元素：`[` 单独成行或 `['id' => 1, ...]` 整行写成元素，都
            # 用父数组中的递增序号作为虚拟键，让 `'levels' => [ [...], ... ]`
            # 这类活动配置能按 1、2、3 分级枚举。
            parent = stack[-1]
            parent.child_index += 1
            stack.append(
                _OpenGroup(
                    indent=indent,
                    key=str(parent.child_index),
                    test_mode=False,
                    branch=parent.branch,
                    comment=group_comment,
                )
            )
            if group_comment:
                group_comments.setdefault(
                    tuple(entry.key for entry in stack), group_comment
                )

        # 本行直接所属的数组路径。行内嵌套数组（`"name" => [`）的键值在下面
        # 按字符位置单独归属，不能把同一行的 id/limit 也并进嵌套层级。
        base_path = (
            path_before_close
            if close_then_siblings and path_before_close
            else tuple(entry.key for entry in stack)
        )
        base_branch = (
            branch_before_close
            if close_then_siblings
            else (stack[-1].branch if stack else "")
        )
        # 记录每行所属的数组路径，供 `getByTestMode` 环境值补全等级前缀。
        # 否则 `score => getByTestMode(测试, 正式)` 只能保存键名，无法归属到
        # 具体等级，答案里就没有门槛值。
        line_paths[item.line] = base_path
        inline_keys = []
        for order, match in enumerate(_INLINE_GROUP_PATTERN.finditer(stripped)):
            # 第一个匹配通常就是行首数组开放符，已经在上方处理，跳过。
            if order == 0 and group:
                continue
            inline_keys.append((match.start(), match.group("key")))

        def path_for(position: int) -> Tuple[str, ...]:
            """按字符位置计算键值所属的数组路径。

            参数:
                - `position`：键值在本行去除缩进后的字符位置，类型为 `int`。

            Returns:
                本行直接路径加上位于该位置之前的行内嵌套键。用于让同一行里的
                外层 `id/limit` 与内层 `name.zh`、`prizes` 归属于正确层级。
            """
            nested = tuple(key for start, key in inline_keys if start < position)
            return (*base_path, *nested)

        comment = _inline_comment(content)
        # 数组成员计数：当前行在哪个数组内出现键值，就计入哪个数组。成员值即使
        # 因安全过滤不能展示，也仍然说明该数组存在这个成员。
        for key_match in _ASSIGNMENT_KEY_PATTERN.finditer(stripped):
            # 行首数组开放符本身不是父数组的成员，不能把组名计入自己的成员数。
            if group and key_match.group("key") == group.group("key"):
                continue
            entry_path = path_for(key_match.start())
            if entry_path:
                member_keys.setdefault(entry_path, set()).add(key_match.group("key"))
        matches = list(_LITERAL_ENTRY_PATTERN.finditer(content))
        matches.extend(_CONSTANT_ENTRY_PATTERN.finditer(content))
        for match in matches:
            key = match.group("key")
            raw_value = match.group("value")
            value_text = (
                _safe_constant(raw_value)
                if "::" in raw_value
                else _safe_literal_text(raw_value)
            )
            if not value_text:
                continue
            entry_path = (*path_for(match.start()), key)
            signature = (item.file, item.line, entry_path, value_text, base_branch)
            if signature in seen:
                continue
            seen.add(signature)
            entries.append(
                ConfigEntry(
                    file=file_name,
                    line=item.line,
                    path=entry_path,
                    value_text=value_text,
                    comment=comment,
                    content=stripped,
                    branch=base_branch,
                )
            )
        # 单行标量列表：`'prizes' => [110,120]`，为每个值建立带序号路径的节点，
        # 使档位奖励可以继续解析到同文件的奖品定义表。
        scalar_list = _INLINE_SCALAR_LIST_PATTERN.search(content)
        if scalar_list and "=>" not in scalar_list.group("body"):
            list_key = scalar_list.group("key")
            key_prefix = path_for(scalar_list.start())
            # 行首数字键数组（`1 => [10,30]`）已经压入同名层级，再追加会重复。
            if not (group and list_key == group.group("key")):
                key_prefix = (*key_prefix, list_key)
            for index, raw_item in enumerate(
                scalar_list.group("body").split(","), start=1
            ):
                value_text = _safe_literal_text(raw_item.strip())
                if not value_text:
                    continue
                entry_path = (*key_prefix, str(index))
                signature = (item.file, item.line, entry_path, value_text, base_branch)
                if signature in seen:
                    continue
                seen.add(signature)
                entries.append(
                    ConfigEntry(
                        file=file_name,
                        line=item.line,
                        path=entry_path,
                        value_text=value_text,
                        comment=comment,
                        content=stripped,
                        branch=base_branch,
                    )
                )
            member_keys.setdefault(key_prefix, set()).update(
                str(index)
                for index in range(1, len(scalar_list.group("body").split(",")) + 1)
            )
        # 单行匿名标量列表项（`[1, 5, 150],`）：每个值按位置作为该列表元素的
        # 子项保存。固定档/惊喜档的区间配置都采用这种写法，旧解析器完全读不到。
        single_item = re.fullmatch(r"\[(?P<body>[^\[\]]*)\]\s*,?", stripped)
        if (
            stack
            and single_item
            and "=>" not in single_item.group("body")
            and not scalar_list
        ):
            for index, raw_item in enumerate(
                single_item.group("body").split(","), start=1
            ):
                value_text = _safe_literal_text(raw_item.strip())
                if not value_text:
                    continue
                entry_path = (*base_path, str(index))
                signature = (item.file, item.line, entry_path, value_text, base_branch)
                if signature in seen:
                    continue
                seen.add(signature)
                entries.append(
                    ConfigEntry(
                        file=file_name,
                        line=item.line,
                        path=entry_path,
                        value_text=value_text,
                        comment=comment,
                        content=stripped,
                        branch=base_branch,
                    )
                )
            member_keys.setdefault(base_path, set()).update(
                str(index)
                for index in range(1, len(single_item.group("body").split(",")) + 1)
            )
        # 多行标量列表：`'allow_types' => [` 之后每行一个值。只有当前行没有
        # 键值对且能完整匹配单个标量时才计数，避免把普通延续行误当成员。
        if (
            stack
            and not matches
            and not scalar_list
            and _SCALAR_ITEM_PATTERN.match(stripped)
        ):
            value_text = _safe_literal_text(
                _SCALAR_ITEM_PATTERN.match(stripped).group("value")
            )
            if value_text:
                frame = stack[-1]
                frame.scalar_index += 1
                entry_path = (*base_path, str(frame.scalar_index))
                signature = (item.file, item.line, entry_path, value_text, base_branch)
                if signature not in seen:
                    seen.add(signature)
                    entries.append(
                        ConfigEntry(
                            file=file_name,
                            line=item.line,
                            path=entry_path,
                            value_text=value_text,
                            comment=comment,
                            content=stripped,
                            branch=base_branch,
                        )
                    )
                member_keys.setdefault(base_path, set()).add(str(frame.scalar_index))
        # 表达式日期：把 `modify('2026-09-23 12:00:00')` 这类活动起止时间转成
        # 可验证的日期字面值。仅当该行是键值赋值且没有已解析的字面量时生效，
        # 避免重复记录同一键。
        if not matches and not scalar_list:
            assignment = _ASSIGNMENT_KEY_PATTERN.match(stripped)
            date_match = _DATE_LITERAL_PATTERN.search(content)
            if assignment and date_match:
                entry_path = (*path_for(assignment.start()), assignment.group("key"))
                signature = (
                    item.file,
                    item.line,
                    entry_path,
                    date_match.group(1),
                    base_branch,
                )
                if signature not in seen:
                    seen.add(signature)
                    entries.append(
                        ConfigEntry(
                            file=file_name,
                            line=item.line,
                            path=entry_path,
                            value_text=date_match.group(1),
                            comment=comment,
                            content=stripped,
                            branch=base_branch,
                        )
                    )
        # 行内嵌套数组的栈帧放在最后补入，供下一行的多行子内容继续使用；
        # 使用本行缩进而不是更深缩进，保证下一行子内容不会被提前弹出。
        for _, inline_key in inline_keys:
            stack.append(
                _OpenGroup(
                    indent=indent,
                    key=inline_key,
                    test_mode=False,
                    branch=base_branch,
                    comment="",
                )
            )
        recent_comments = []
    counts = {key: len(value) for key, value in member_keys.items()}
    return entries, counts, group_comments, line_paths


def _parse_env_values(
    file_name: str,
    file_lines: Sequence[EvidenceLine],
    line_paths: Optional[Dict[int, Tuple[str, ...]]] = None,
) -> List[ConfigEnvValue]:
    """解析单行 `getByTestMode(测试值, 正式值)` 配置。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。
        - `file_lines`：同一文件的证据行，类型为 `Sequence[EvidenceLine]`；只读取
          当前证据窗口，不访问窗口外源码。
        - `line_paths`：行号到所属数组路径的映射，类型为
          `Optional[Dict[int, Tuple[str, ...]]]`；由 `_parse_file_entries` 生成。
          等级表里的 `score => getByTestMode(...)` 只有带上等级前缀
          （如 `level.1.score`）才能归入对应等级，否则会被当成全局同名键。

    Returns:
        环境覆盖配置列表。多行 URL 配置会在第一行命中键名和函数名，但值无法
        完整解析时不生成事实，避免把半截地址写入报告。
    """
    values: List[ConfigEnvValue] = []
    prefix_map = line_paths or {}
    for item in sorted(file_lines, key=lambda row: row.line):
        for match in _ENV_VALUE_PATTERN.finditer(item.content):
            test_value = _unquote_env_value(match.group("test"))
            production_value = _unquote_env_value(match.group("prod"))
            if test_value is None or production_value is None:
                continue
            key = match.group("key")
            values.append(
                ConfigEnvValue(
                    file=file_name,
                    line=item.line,
                    path=(*prefix_map.get(item.line, ()), key),
                    test_value=test_value,
                    production_value=production_value,
                    comment=_inline_comment(item.content),
                )
            )
    return values


def _unquote_env_value(raw_value: str) -> Optional[str]:
    """去掉环境值两侧引号并返回内部文本。

    参数:
        - `raw_value`：正则捕获的 PHP 字面值，类型为 `str`；可能是单引号、
          双引号、数字或布尔值。

    Returns:
        去掉引号后的内部文本；输入格式无法识别时返回 `None`，由调用方跳过该条
        配置，避免把表达式的一部分当成真实值。
    """
    text = str(raw_value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    if text.casefold() in {"true", "false"} or re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return text
    return None


def _render_env_value(raw_value: str, key: str) -> str:
    """把环境值渲染为可安全写入报告的形式。

    参数:
        - `raw_value`：已去掉引号的环境值，类型为 `str`。
        - `key`：配置键名，类型为 `str`；用于判断字段是否涉及密钥或令牌。

    返回:
        面向用户的展示文本。空值显示“空”；敏感字段只显示“已配置（敏感值不
        展示）”；URL、路径、邮箱等非普通标识符显示“已配置（地址或特殊值不展示）”；
        普通业务标识符原样展示。
    """
    if not raw_value:
        return "空"
    if _SENSITIVE_KEY_PATTERN.search(key):
        return "已配置（敏感值不展示）"
    if not _SAFE_ENV_VALUE_PATTERN.fullmatch(raw_value):
        return "已配置（地址或特殊值不展示）"
    return f"`{raw_value}`"


def _env_value_score(
    item: ConfigEnvValue,
    anchors: Sequence[str],
    concepts: set,
    question: str,
) -> int:
    """计算一条环境配置与问题的相关度。

    参数:
        - `item`：待排序的环境配置，类型为 `ConfigEnvValue`。
        - `anchors`：问题提取出的中文业务片段，类型为 `Sequence[str]`。
        - `concepts`：问题识别出的可组合业务概念名，类型为 `set`。
        - `question`：用户原始问题，类型为 `str`；用于识别用户直接写出的配置键。

    Returns:
        非负分值。配置键、注释和文件路径与问题重合时得分；问题直接用反引号或
        英文标识符写出键名时额外加权，不做项目名或活动名硬编码。
    """
    context = " ".join(
        [item.key, item.comment, item.file, " ".join(item.path)]
    ).casefold()
    score = 0
    for anchor in anchors:
        value = anchor.casefold()
        if value and value in context:
            score += len(value) * (3 if len(value) >= 4 else 1)
    context_concepts = {
        concept.name for concept in detect_semantic_concepts(context)
    }
    score += len(concepts.intersection(context_concepts)) * 8
    if re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(item.key)}(?![A-Za-z0-9_])",
        question,
        re.IGNORECASE,
    ):
        score += 40
    return score


def _question_focus_keys(question: str) -> Optional[set]:
    """按问题点名的取值类别生成需要展示的配置键集合。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        问题只点名一个取值类别时返回该类别对应的键名集合；没有明确类别或同时
        点名多个类别（“门槛和奖励”“门槛与瓜分规则”）时返回 `None`，表示保留
        所有已通过安全过滤的非技术字段。多类别问题需要完整上下文，不能为了
        精简而漏掉用户同时追问的字段。
    """
    matched: List[set] = []
    for intent, keys in _FOCUS_KEY_SETS.items():
        if intent in question:
            matched.append(keys)
    if len(matched) != 1:
        return None
    return set(matched[0])


def _is_name_like(entry: ConfigEntry) -> bool:
    """判断配置节点是否属于名称类字段。

    参数:
        - `entry`：配置图中的字面量节点，类型为 `ConfigEntry`。

    Returns:
        节点本身是 `name/title/desc`，或它是这些字段下的多语言子键
        （`name.zh`、`title.en`）时返回 `True`。名称类字段在结论里合并展示，
        避免同一名称出现 `en=`、`in=`、`zh=` 三份重复文本。
    """
    key = entry.key.casefold()
    if key in _NAME_GROUP_KEYS:
        return True
    if len(entry.path) >= 2:
        parent = entry.path[-2].casefold()
        return parent in _NAME_GROUP_KEYS and key in _LANGUAGE_KEYS
    return False


def _name_text(entries: Sequence[ConfigEntry]) -> str:
    """从一组配置节点中挑选最合适的展示名称。

    参数:
        - `entries`：同一等级的配置节点，类型为 `Sequence[ConfigEntry]`。

    Returns:
        优先返回中文名称（`zh/tw/hk`），其次返回首个英文名称；没有名称字段时
        返回空字符串。只返回源码中已通过安全过滤的字面值，不生成翻译。
    """
    chinese = ""
    first = ""
    for entry in entries:
        if not _is_name_like(entry):
            continue
        value = entry.value_text.strip()
        if not value:
            continue
        key = entry.key.casefold()
        if key in {"zh", "tw", "hk"} and not chinese:
            chinese = value
        elif not first:
            first = value
    return chinese or first


def _render_env_pair(item: ConfigEnvValue) -> str:
    """把环境配置渲染为“测试/正式”可读文本。

    参数:
        - `item`：`getByTestMode` 环境配置，类型为 `ConfigEnvValue`；测试值和
          正式值均已去掉引号。

    Returns:
        测试值和正式值相同时只展示一个值；不同时同时标注两个分支。敏感字段
        由调用方提前过滤，本函数只处理已允许展示的键。
    """
    label = _ENTRY_LABELS.get(item.key.casefold(), item.key)
    test_value = item.test_value or "空"
    production_value = item.production_value or "空"
    if test_value == production_value:
        return f"{label}={test_value}"
    return f"{label}（测试={test_value}，正式={production_value}）"


class ConfigGraph:
    """保存一次诊断证据中的配置节点，并提供通用枚举查询。"""

    def __init__(
        self,
        entries: Sequence[ConfigEntry],
        env_values: Sequence[ConfigEnvValue] = (),
        member_counts: Optional[Dict[Tuple[str, ...], int]] = None,
        group_comments: Optional[Dict[Tuple[str, ...], str]] = None,
    ) -> None:
        """初始化配置图。

        参数:
            - `entries`：`parse_config_graph` 生成的配置节点，类型为
              `Sequence[ConfigEntry]`；只包含已通过敏感值过滤的字面量。
            - `env_values`：`getByTestMode` 环境覆盖值，类型为
              `Sequence[ConfigEnvValue]`；渲染时会再次做敏感字段脱敏。
            - `member_counts`：数组路径到直接成员数量的映射，类型为
              `Optional[Dict[Tuple[str, ...], int]]`；键的第一个元素是文件名，
              其余是数组路径。用于回答“配置了多少个”这类计数问题，成员值本身
              可能因安全过滤未进入节点表。
            - `group_comments`：数组路径到开放行前中文注释的映射，类型为
              `Optional[Dict[Tuple[str, ...], str]]`；用于判断中文问题指向哪个
              配置数组，例如“任务送礼阶段奖励”对应 `levels`。
        """
        self._entries = tuple(entries)
        self._env_values = tuple(env_values)
        self._member_counts = dict(member_counts or {})
        self._group_comments = dict(group_comments or {})
        # 建立“文件 + 父路径 + 数字键 → 节点”索引，用于把 `id=100` 这类引用
        # 解析到同文件 `prizes[100]` 的定义。该机制不依赖具体业务字段名，
        # 新活动仍使用引用表时不需要新增分析器。
        numeric_groups: Dict[Tuple[str, Tuple[str, ...]], Dict[str, List[ConfigEntry]]] = {}
        for entry in self._entries:
            for position, segment in enumerate(entry.path):
                if not segment.isdigit():
                    continue
                group_key = (entry.file, entry.path[:position])
                numeric_groups.setdefault(group_key, {}).setdefault(
                    segment, []
                ).append(entry)
        self._numeric_groups = numeric_groups

    @property
    def entries(self) -> Tuple[ConfigEntry, ...]:
        """返回全部配置节点，按文件和行号稳定排序。"""
        return self._entries

    @property
    def env_values(self) -> Tuple[ConfigEnvValue, ...]:
        """返回全部环境覆盖配置，按文件和行号稳定排序。"""
        return self._env_values

    def env_value_facts(self, question: str) -> List[Dict[str, object]]:
        """按问题语义返回测试/正式环境配置值事实。

        参数:
            - `question`：用户原始业务问题，类型为 `str`；用于选择与问题业务
              锚点、配置键或注释最相关的环境配置。

        Returns:
            最多六条环境配置事实。每条事实明确区分测试值和正式值，敏感字段和
            地址类值只说明“已配置”，不会输出密钥、令牌或内网地址明文。
        """
        if not self._env_values:
            return []
        anchors = [
            value
            for value in extract_generic_anchors(question, max_anchors=32)
            if len(value) >= 2
        ]
        concepts = {item.name for item in detect_semantic_concepts(question)}
        scored = []
        for order, item in enumerate(self._env_values):
            score = _env_value_score(item, anchors, concepts, question)
            if score <= 0:
                continue
            scored.append((score, order, item))
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        facts: List[Dict[str, object]] = []
        for _, _, item in scored[:6]:
            facts.append(
                {
                    "category": "环境配置值",
                    "statement": (
                        f"配置 `{'.'.join(item.path)}` 的测试环境值为 "
                        f"{_render_env_value(item.test_value, item.key)}，"
                        f"正式环境值为 "
                        f"{_render_env_value(item.production_value, item.key)}。"
                        + (f" 配置注释：{item.comment}。" if item.comment else "")
                    ),
                    "evidence_refs": [item.ref],
                }
            )
        return facts

    @property
    def files(self) -> Tuple[str, ...]:
        """返回出现配置节点的文件列表，保持首次出现顺序。"""
        return tuple(dict.fromkeys(item.file for item in self._entries))

    def enumeration_facts(self, question: str) -> List[Dict[str, object]]:
        """按问题中的等级/档位语义枚举重复数字键分组。

        参数:
            - `question`：用户原始业务问题，类型为 `str`；其中“每个/所有/列出”
              等通用枚举词和业务锚点用于选择目标分组，不绑定具体活动或类名。

        Returns:
            每个等级一条事实，包含 `category`、`statement` 和
            `evidence_refs`。没有重复数字键分组时，若问题在询问数量，则返回
            数组成员计数事实；证据与问题完全无关时返回空列表，由上层继续使用
            配置取值或其他分析器。
        """
        if not self._entries and not self._member_counts:
            return []
        anchors = [
            value
            for value in extract_generic_anchors(question, max_anchors=48)
            if len(value) >= 2
        ]
        concepts = {item.name for item in detect_semantic_concepts(question)}
        # 问题同时点名多个不同业务字段（“初始金币、进池比例、公会数分别是多少”）
        # 时，重复数组枚举无法同时覆盖这些标量键；让上层改用配置取值路径。
        intent_words = [
            word for word in _KEY_INTENT_ALIASES if word in question
        ]
        has_structure = any(
            token in question
            for token in ("等级", "档位", "级别", "阶段", "轮", "前三", "日榜", "兜底")
        ) or bool(
            # “第 1 名/第 2 档/第 3 级”同样点名重复结构中的单个条目标识。
            re.search(r"第\s*[\d一二三四五六七八九十]+\s*(?:名|档|级|轮|项)", question)
        )
        # “监听哪些游戏类型/发奖”虽然有多个意图词，但它们是同一数组的
        # 近义描述，应继续走计数/枚举；只有“分别是多少”这类多字段取值问题才
        # 需要让上层改用配置取值路径。
        enumeration_ask = any(
            token in question for token in ("哪些", "哪几", "几种", "哪几种", "有哪几")
        )
        if len(intent_words) >= 2 and not has_structure and not enumeration_ask:
            return []
        # “配置了几个指定礼物/多少种监听类型”询问的是数组成员数量。只有在问题
        # 同时追问档位明细（门槛/奖励/比例/库存/价格等）时才必须走枚举；否则
        # 先给出计数和成员取值，避免被同文件里恰好存在的等级数组抢答。
        value_intents = (
            "门槛",
            "奖励",
            "奖品",
            "比例",
            "库存",
            "价格",
            "上限",
            "下限",
            "目标",
            "概率",
            "名次",
            "排名",
            "积分",
            "金额",
            "碎片",
            "燃料",
            "宝箱",
            "时间",
            "日期",
        )
        if (
            any(word in question for word in _COUNT_QUESTION_WORDS)
            and not any(word in question for word in value_intents)
            and not has_structure
        ):
            count_facts = self._count_facts(question, anchors, concepts)
            if count_facts:
                return count_facts
        candidates = self._enumeration_candidates(anchors, concepts, question)
        if not candidates:
            return self._count_facts(question, anchors, concepts)
        best = candidates[0]
        # 问题没有任何业务锚点或概念时，只有当配置本身带“等级/档位”字样才
        # 接受枚举，避免把任意重复数字键数组都当成业务等级回答。
        if best.score <= 0 and not any(
            token in question.casefold() for token in _ENUMERATION_WORDS
        ):
            return []
        return self._render_levels(best, question)

    def _enumeration_candidates(
        self,
        anchors: Sequence[str],
        concepts: set[str],
        question: str = "",
    ) -> List[_EnumerationCandidate]:
        """发现所有可能的重复数字键分组并按问题相关度排序。

        参数:
            - `anchors`：问题提取出的中文业务片段，类型为 `Sequence[str]`。
            - `concepts`：问题识别出的可组合业务概念名，类型为 `set[str]`。
            - `question`：用户原始问题，类型为 `str`；用于识别问题里直接写出的
              配置键、数量约束和序数。

        Returns:
            按相关度从高到低排列的候选分组。仅保留同一父路径下至少两个等级、
            且等级值可比较的分组。
        """
        by_file: Dict[str, List[ConfigEntry]] = {}
        for entry in self._entries:
            by_file.setdefault(entry.file, []).append(entry)

        candidates: List[_EnumerationCandidate] = []
        for file_name, file_entries in by_file.items():
            positions: Dict[int, Dict[int, List[ConfigEntry]]] = {}
            for entry in file_entries:
                for position, segment in enumerate(entry.path):
                    if not segment.isdigit():
                        continue
                    levels = positions.setdefault(position, {})
                    levels.setdefault(int(segment), []).append(entry)
            for position, levels in positions.items():
                numeric_levels = sorted(levels)
                if len(numeric_levels) < 2:
                    continue
                # 只接受同一父路径下的等级分组，避免把互不相关的数字键拼在一起。
                grouped: Dict[Tuple[str, ...], List[ConfigEntry]] = {}
                for level, level_entries in levels.items():
                    for entry in level_entries:
                        grouped.setdefault(entry.path[:position], []).append(entry)
                for prefix, entries in grouped.items():
                    level_values = {
                        int(entry.path[position])
                        for entry in entries
                        if len(entry.path) > position
                        and entry.path[position].isdigit()
                    }
                    if len(level_values) < 2:
                        continue
                    score = self._candidate_score(
                        file_name,
                        prefix,
                        entries,
                        anchors,
                        concepts,
                        question,
                    )
                    prefix_folded = " ".join(prefix).casefold()
                    rank_ordinal = _question_rank_ordinal(question)
                    level_ordinal = _question_ordinal(question)
                    # 只认 `top*`/`rank*` 开头的排名数组；`common_prizes`、
                    # `prizes` 等普通奖品表虽然含 `prizes` 字样，但不是名次表。
                    is_rank_prefix = any(
                        segment.casefold().startswith(("top", "rank"))
                        for segment in prefix
                    )
                    # 等级/档位类问题如果没有同时问榜单、名次，就不能选择
                    # `day_rank_top_prizes`、`rank_prizes` 这类排名数组，否则
                    # “第 3 个个人等级”会被第 3 名的日榜奖品抢答。
                    rank_intent = any(
                        token in question
                        for token in ("名次", "排名", "日榜", "周榜", "月榜", "榜", "前")
                    ) or bool(re.search(r"前\s*\d+\s*名", question))
                    level_intent = any(
                        token in question for token in ("等级", "档位", "档", "级别")
                    )
                    if level_intent and not rank_intent and any(
                        token in prefix_folded
                        for token in ("day_rank", "top_prizes", "rank_prizes")
                    ):
                        continue
                    is_level_group = (
                        any(
                            entry.key.casefold() in {"limit", "value", "target"}
                            for entry in entries
                        )
                        or "levels" in prefix_folded
                        or "level" in prefix_folded
                        or "等级" in self._group_comments.get(prefix, "")
                        or "档" in self._group_comments.get(prefix, "")
                    )
                    # 名次序号只对排名/奖品表生效，等级序号只对等级表生效；
                    # 否则同文件里恰好含相同数字键的编码数组会获得同样加分。
                    if (
                        rank_ordinal is not None
                        and rank_ordinal in level_values
                        and is_rank_prefix
                    ):
                        score += 25
                    # 名次问题下，普通奖品明细表（`prizes[100..109]`）不包含
                    # 名次编号，不能和真正的 `rank_prizes[1..10]` 同权参与排序。
                    if (
                        rank_ordinal is not None
                        and not is_rank_prefix
                        and "prizes" in prefix_folded
                        and rank_ordinal not in level_values
                    ):
                        score -= 10
                    elif (
                        level_ordinal is not None
                        and level_ordinal in level_values
                        and (is_level_group or not is_rank_prefix)
                    ):
                        score += 25
                    # “兜底奖/保底奖”问的是带 `isFallback` 标记的那一项；
                    # 优先包含兜底开关的分组，而不是普通奖品明细。
                    # “前三/前十/前 10 名”问的是名次奖励表
                    # （`top_prizes`/`rank_prizes`），不是同名的普通奖励表；
                    # 等级数量更多的普奖表不能因为条目多就在排序中胜出。
                    if (
                        any(
                            token in question
                            for token in ("前三", "前五", "前十", "名次", "排名", "日榜")
                        )
                        or re.search(r"前\s*\d+\s*名", question)
                    ) and is_rank_prefix:
                        score += 30
                    if any(token in question for token in ("兜底", "保底")) and any(
                        entry.key.casefold() in {"isfallback", "is_fallback", "fallback"}
                        and entry.value_text.strip("'\" ").casefold() in {"1", "true"}
                        for entry in entries
                    ):
                        score += 45
                    candidates.append(
                        _EnumerationCandidate(
                            file=file_name,
                            position=position,
                            prefix=prefix,
                            levels=tuple(sorted(level_values)),
                            entries=tuple(entries),
                            score=score,
                        )
                    )
        candidates.sort(
            key=lambda item: (
                -item.score,
                # 同分时优先外层分组：`prize.1/prize.6` 才是用户说的“每个等级”，
                # 内层数组元素序号（1、2、3）只是同一等级里的奖品位置。
                item.position,
                -len(item.levels),
                item.file,
            )
        )
        return candidates

    def _candidate_score(
        self,
        file_name: str,
        prefix: Tuple[str, ...],
        entries: Sequence[ConfigEntry],
        anchors: Sequence[str],
        concepts: set[str],
        question: str = "",
    ) -> int:
        """计算一个等级分组与用户问题的相关度。

        参数:
            - `file_name`：配置文件相对路径，类型为 `str`。
            - `prefix`：等级数字键之前的数组路径，类型为 `Tuple[str, ...]`。
            - `entries`：该父路径下属于各个等级的配置节点，类型为
              `Sequence[ConfigEntry]`。
            - `anchors`：问题中的中文业务片段，类型为 `Sequence[str]`。
            - `concepts`：问题中的可组合业务概念名，类型为 `set[str]`。
            - `question`：用户原始问题，类型为 `str`；用于识别问题直接写出的
              配置键和数量约束。

        Returns:
            用于候选排序的非负分值。文件路径、数组路径、数组前中文注释、
            问题中显式键名和数量约束都会提高相关度；不包含项目名或活动名的
            硬编码权重。
        """
        # 数组开放行前的中文注释最能说明该数组的业务含义（例如
        # `// 任务送礼阶段奖励`），必须与成员内容一起参与匹配。
        group_comment = self._group_comments.get(prefix, "")
        context = " ".join(
            [
                file_name,
                " ".join(prefix),
                group_comment,
                *[
                    value
                    for entry in entries
                    for value in (entry.content, entry.comment, entry.value_text)
                    if value
                ],
            ]
        ).casefold()
        score = 0
        for anchor in anchors:
            value = anchor.casefold()
            if value and value in context:
                score += len(value) * (3 if len(value) >= 4 else 1)
        context_concepts = {
            concept.name for concept in detect_semantic_concepts(context)
        }
        score += len(concepts.intersection(context_concepts)) * 8
        if any(token in context for token in ("等级", "档位", "level", "grade", "tier")):
            score += 6
        # 问题里的结构词（阶段/日榜/兜底/前三）如果出现在数组前注释中，说明
        # 该数组就是用户点名的结构；同一文件里其他带奖品名的数组不应抢答。
        structural_words = [
            token
            for token in ("阶段", "轮", "名次", "排名", "前三", "日榜", "兜底", "等级", "档位")
            if token in question
        ]
        if structural_words and any(
            word in group_comment for word in structural_words
        ):
            score += 25
        # 问题直接点名数组标题（例如“神秘遗迹”与 `//神秘遗迹配置`）时，该数组
        # 比其它同样带“阶段/等级”字样的数组更具体。四字以上锚点命中标题时
        # 覆盖结构词的通用加分，避免子配置被主配置抢先。
        named_group_anchors = [
            anchor
            for anchor in anchors
            if len(anchor) >= 4 and anchor.casefold() in group_comment.casefold()
        ]
        if named_group_anchors:
            score += 35
        # 中文业务意图与字段名别名（日榜→top_prizes、兜底→isFallback 等）命中
        # 时提高该数组优先级，避免同文件里的个人等级表抢答排名类问题。
        for intent, aliases in _KEY_INTENT_ALIASES.items():
            if intent in question and any(
                alias in context for alias in aliases
            ):
                score += 30
                break
        # 问题直接写出的配置键（limit、target、pro、stock 等）命中分组成员时，
        # 说明该分组正是用户询问的那张配置表。
        key_terms = _question_key_terms(question)
        if key_terms:
            entry_keys = {
                entry.key.casefold()
                for entry in entries
            }
            if key_terms.intersection(entry_keys):
                score += 24
        # “六个等级/前 10 名/3 轮”这类数量约束与分组条目数一致时优先选择。
        count_hint = _question_count_hint(question)
        if count_hint:
            level_values = {
                entry.path[-2]
                for entry in entries
                if len(entry.path) >= 2
            }
            if len(level_values) == count_hint:
                # 数量约束与分组条目数一致是很强的结构信号（“四个阶段”“前 10 名”），
                # 与名次/等级意图分值同级，避免条目更多的无关数组胜出。
                score += 25
        return score

    def _count_facts(
        self,
        question: str,
        anchors: Sequence[str],
        concepts: set[str],
    ) -> List[Dict[str, object]]:
        """在问题只询问数量时返回数组成员计数事实。

        参数:
            - `question`：用户原始业务问题，类型为 `str`；只有包含“多少/几/哪些”
              等数量或列举词时才生成计数事实。
            - `anchors`：问题中的中文业务片段，类型为 `Sequence[str]`。
            - `concepts`：问题中的可组合业务概念名，类型为 `set[str]`。

        Returns:
            最多一条计数事实。用于回答国旗数量、监听类型数量等“没有等级键但
            需要统计成员”的问题；数组路径、组注释和问题锚点都不匹配时返回空
            列表，避免把无关数组的数量当成答案。
        """
        if not any(word in question for word in _COUNT_QUESTION_WORDS):
            return []
        scored = []
        for order, ((file_name, *path), count) in enumerate(
            self._member_counts.items()
        ):
            if count < 2 or any(segment.isdigit() for segment in path):
                continue
            prefix = tuple(path)
            context = " ".join(
                [file_name, " ".join(prefix), self._group_comments.get(prefix, "")]
            ).casefold()
            score = 0
            for anchor in anchors:
                value = anchor.casefold()
                if value and value in context:
                    score += len(value) * (3 if len(value) >= 4 else 1)
            context_concepts = {
                concept.name for concept in detect_semantic_concepts(context)
            }
            score += len(concepts.intersection(context_concepts)) * 8
            # 中文意图别名（礼物→gifts、宝箱→box 等）命中的数组就是用户点名
            # 的对象。注释可能使用繁体或不同于问题的业务措辞，别名比逐字锚点
            # 更稳定，因此和等级分组一样给予意图加分。
            for intent, aliases in _KEY_INTENT_ALIASES.items():
                if intent in question and any(alias in context for alias in aliases):
                    score += 30
                    break
            # 计数事实必须与问题有足够强的业务锚点重合。仅靠一个宽泛概念
            # （例如“奖励”命中任意 prizes 表）就给出数量，容易答到无关数组。
            if score < 10:
                continue
            scored.append((score, order, file_name, prefix, count))
        if not scored:
            return []
        scored.sort(key=lambda item: (-item[0], item[1]))
        _, _, file_name, prefix, count = scored[0]
        comment = self._group_comments.get(prefix, "")
        statement = f"配置 `{'.'.join(prefix)}` 共 {count} 项。"
        # 数量问题常同时追问“分别是哪些”（指定礼物 ID、监听类型编码）。
        # 只补充紧随该数组的直接数字成员，最多 12 个，避免把整张奖励表或
        # 名称字符串倾倒进结论。
        member_values = sorted(
            {
                entry.value_text
                for entry in self._entries
                if entry.file == file_name
                and len(prefix) < len(entry.path) <= len(prefix) + 2
                and entry.path[: len(prefix)] == prefix
                and entry.value_text.isdigit()
            },
            key=lambda value: int(value),
        )[:12]
        if member_values:
            statement += " 成员取值：" + "、".join(member_values) + "。"
        if comment:
            statement += f" 配置注释：{comment}。"
        return [
            {
                "category": "配置计数",
                "statement": statement,
                "evidence_refs": [
                    entry.ref
                    for entry in self._entries
                    if entry.file == file_name
                    and len(entry.path) > len(prefix)
                    and entry.path[: len(prefix)] == prefix
                ][:3],
            }
        ]

    def _render_levels(
        self,
        candidate: _EnumerationCandidate,
        question: str,
    ) -> List[Dict[str, object]]:
        """把选中的等级分组渲染为逐级事实。

        参数:
            - `candidate`：已选中的等级分组候选，类型为
              `_EnumerationCandidate`。
            - `question`：用户原始问题，类型为 `str`；用于决定只展示测试分支、
              只展示正式分支，还是同时标注两个分支。

        Returns:
            每个等级一条事实。同一行内的多个键值对合并为同一项；分支不一致时
            在语句中显式标注，避免把正式配置冒充测试配置。
        """
        folded_question = question.casefold()
        if "测试" in question:
            wanted_branches = {_TEST_BRANCH, ""}
            branch_label = ""
        elif any(token in question for token in ("正式", "生产")):
            wanted_branches = {_PRODUCTION_BRANCH, ""}
            branch_label = ""
        else:
            wanted_branches = {_TEST_BRANCH, _PRODUCTION_BRANCH, ""}
            branch_label = "环境分支"

        by_level: Dict[int, List[ConfigEntry]] = {}
        for entry in candidate.entries:
            if len(entry.path) <= candidate.position:
                continue
            # 只渲染与候选前缀完全一致的节点。同一文件里 `levels[3]` 和
            # `magicAlchemy[3]` 都带数字 3，若不校验前缀，后者的价格、概率
            # 字段会被拼进前者的等级结论。
            if entry.path[: candidate.position] != candidate.prefix:
                continue
            raw_level = entry.path[candidate.position]
            if not raw_level.isdigit():
                continue
            if entry.branch not in wanted_branches:
                continue
            by_level.setdefault(int(raw_level), []).append(entry)

        # “哪些阶段支持跳转 X”问的是布尔开关为真的子集，不是全部阶段。
        # 只有分组里确实存在跳转类键时才过滤，普通等级问题不受影响。
        if any(token in question for token in ("跳转", "支持")):
            jump_keys = {
                "is_can_jump_x",
                "can_jump_x",
                "jump_x",
                "is_can_jump",
                "can_jump",
            }
            if any(entry.key.casefold() in jump_keys for entry in candidate.entries):
                supported = {
                    int(entry.path[candidate.position])
                    for entry in candidate.entries
                    if len(entry.path) > candidate.position
                    and entry.path[candidate.position].isdigit()
                    and entry.key.casefold() in jump_keys
                    and entry.value_text.strip("'\" ").casefold() in {"1", "true"}
                }
                if supported:
                    by_level = {
                        level: entries
                        for level, entries in by_level.items()
                        if level in supported
                    }

        facts: List[Dict[str, object]] = []
        parent_label = candidate.prefix[-1] if candidate.prefix else "配置"
        # 名次表和等级表都用数字键，但用户语义不同。前缀含 rank/top，或问题
        # 明确写“第 N 名/前 N 名”时，结论用“名次”而不是“等级”。
        prefix_folded = " ".join(candidate.prefix).casefold()
        rank_group = any(
            token in prefix_folded for token in ("rank", "top")
        ) or bool(re.search(r"第\s*\d+\s*名|前\s*\d+\s*名", question))
        ordinal_label = "名次" if rank_group else "等级"
        # 问题只问门槛时不再输出价格、折扣、图片等无关字段，让每个等级只剩
        # 用户真正要的数值；问题没有点名类别时保留全部可见字段。
        focus_keys = _question_focus_keys(question)
        # 用户点名“第 3 个等级”时只渲染该等级，避免把整张配置表当成答案。
        ordinal = _question_ordinal(question)
        levels = sorted(by_level)[:_MAX_ENUMERATED_LEVELS]
        if ordinal is not None:
            if ordinal in levels:
                levels = [ordinal]
            elif 1 <= ordinal <= len(levels):
                # 序号也可能指数组位置而不是键值（奖励表用 600..603 编号）。
                # 键值没有命中时按位置取第 N 项，避免结论为空。
                levels = [levels[ordinal - 1]]
            else:
                levels = []
        # “前 3 名”只回答前三名，不能把同文件 1-10 名整张奖励表都输出。
        top_count = _question_top_count(question)
        if top_count is not None and ordinal is None and levels:
            levels = [value for value in levels if value <= top_count][:top_count]
        for level in levels:
            level_entries = sorted(by_level[level], key=lambda item: item.line)
            # 等级门槛常写成 `score => getByTestMode(测试, 正式)`，这类值不会
            # 进入字面量节点表，必须从环境配置中按等级前缀补回，否则结论里
            # 只剩名称和类型，没有用户要的门槛。
            env_values = self._level_env_values(candidate.prefix, level)
            if not level_entries and not env_values:
                continue
            # 只有名次表/奖品表里的 `id` 才指向奖品定义；等级记录自身的
            # `id => 3` 是编号，解析到同文件其他数组会串入无关奖励。
            group_name = candidate.prefix[-1].casefold() if candidate.prefix else ""
            allow_id_reference = any(
                token in group_name for token in ("prize", "rank")
            )
            parts = self._render_entries(
                level_entries,
                candidate.position,
                env_values=env_values,
                focus_keys=focus_keys,
                allow_id_reference=allow_id_reference,
            )
            if not parts:
                continue
            level_name = _name_text(level_entries)
            if not level_name:
                # 部分配置用 `type => 'ranger'` 表达等级标识；没有名称时用
                # 类型标识代替，至少让每个等级可区分。
                for entry in level_entries:
                    if entry.key.casefold() == "type" and entry.value_text:
                        level_name = entry.value_text
                        break
            branch_note = ""
            if branch_label:
                branches = {entry.branch for entry in level_entries if entry.branch}
                if len(branches) == 1:
                    branch_note = (
                        "（测试分支）"
                        if _TEST_BRANCH in branches
                        else "（正式分支）"
                    )
            label_note = f"（{level_name}）" if level_name else ""
            facts.append(
                {
                    "category": f"{parent_label}{ordinal_label}{level}",
                    "statement": (
                        f"{ordinal_label} {level}{label_note}{branch_note}："
                        + "；".join(parts)
                    ),
                    "evidence_refs": list(
                        dict.fromkeys(
                            [entry.ref for entry in level_entries]
                            + [item.ref for item in env_values]
                        )
                    ),
                }
            )
        return facts

    def _level_env_values(
        self,
        prefix: Tuple[str, ...],
        level: int,
    ) -> List[ConfigEnvValue]:
        """读取直属某个等级的 `getByTestMode` 环境配置。

        参数:
            - `prefix`：等级数字键之前的数组路径，类型为 `Tuple[str, ...]`，
              例如 `("level",)`。
            - `level`：当前要渲染的等级序号，类型为 `int`；常量键等级表使用
              解析阶段补入的虚拟序号。

        Returns:
            路径前缀与 `prefix + (str(level),)` 完全一致的环境配置条目。只返回
            当前等级自己的门槛/价格等分支值，不会把其他等级的同名键混进来。
        """
        level_prefix = (*prefix, str(level))
        result: List[ConfigEnvValue] = []
        for item in self._env_values:
            if len(item.path) <= len(level_prefix):
                continue
            if item.path[: len(level_prefix)] != level_prefix:
                continue
            # 多语言名称不是门槛；环境值渲染在 `_render_entries` 中统一过滤。
            result.append(item)
        return result

    def _resolve_reference(self, entry: ConfigEntry) -> List[ConfigEntry]:
        """把数字取值解析到同文件中以该数字为键的定义组。

        参数:
            - `entry`：当前正在渲染的配置节点，类型为 `ConfigEntry`；只有纯数字
              取值才可能是指向奖品表、道具表等定义的引用。

        Returns:
            被引用定义组的配置节点；没有匹配、命中自身祖先组或存在歧义时返回
            空列表。解析只发生在同一配置文件和共同祖先路径内，避免跨活动串值。
        """
        if not entry.value_text.isdigit() or len(entry.value_text) > 6:
            return []
        own_prefix = entry.path[:-1]
        best_entries: List[ConfigEntry] = []
        best_depth = -1
        for (file_name, prefix), levels in self._numeric_groups.items():
            if file_name != entry.file:
                continue
            referenced = levels.get(entry.value_text)
            if not referenced or prefix == own_prefix:
                continue
            # 定义组本身是当前节点的祖先时（例如 `prizes[100].id`），不能再
            # 解析回自己，否则会生成无限嵌套或自我引用。
            if (
                len(entry.path) > len(prefix)
                and entry.path[: len(prefix)] == prefix
            ):
                continue
            common = 0
            for left, right in zip(prefix, own_prefix):
                if left != right:
                    break
                common += 1
            # `id` 是跨数组引用约定：名次表里的 `id` 指向同文件奖品定义表，
            # 即使两者没有共同祖先路径（`rank_prizes.1.0.id` → `prizes.100`），
            # 也必须解析。只接受带 name/type/num 等展示字段的候选定义组，
            # 避免把普通数字 id 误解析成其他编码表。
            is_id_key = entry.key.casefold() == "id"
            if common == 0:
                # 同名引用表：档位数组里的 `prizes` 持有奖品 id，而奖品定义表
                # 通常位于同一文件的顶层 `prizes`。两者没有共同祖先路径，但
                # 定义组名与引用路径中的一段同名，仍可安全解析。
                own_segments = {segment.casefold() for segment in own_prefix}
                if prefix and prefix[-1].casefold() in own_segments:
                    common = 1
                elif is_id_key:
                    referenced_keys = {
                        item.key.casefold() for item in referenced
                    }
                    if referenced_keys.intersection(
                        {"name", "title", "type", "num", "gid", "days"}
                    ):
                        common = 1
            if common == 0 or common < best_depth:
                continue
            if common == best_depth and not (
                is_id_key and len(referenced) > len(best_entries)
            ):
                continue
            best_entries, best_depth = referenced, common
        return best_entries

    @staticmethod
    def _referenced_text(entries: Sequence["ConfigEntry"]) -> str:
        """把被引用定义的叶子值压缩成简短说明。

        参数:
            - `entries`：`_resolve_reference` 返回的定义节点，类型为
              `Sequence[ConfigEntry]`。

        Returns:
            形如 `名称=100美元，type=dollar，num=0` 的说明。`name`、`title`
            等展示字段合并为一项并排在前面，`id`、`gid`、`step`、图片路径和
            多语言重复名称等技术字段不输出；最多保留六个字段，避免单条事实
            过长。
        """
        ordered = sorted(
            entries,
            key=lambda item: (
                0 if _is_name_like(item) else 1,
                item.path,
            ),
        )
        name = _name_text(entries)
        parts: List[str] = ["名称=" + name] if name else []
        for item in ordered:
            key = item.key.casefold()
            if _is_name_like(item) or key in _HIDDEN_ENTRY_KEYS:
                continue
            if key in _LANGUAGE_KEYS:
                continue
            parts.append(f"{key}={item.value_text}")
        return "，".join(parts[:6])

    def _render_entries(
        self,
        entries: Sequence[ConfigEntry],
        level_position: int,
        env_values: Sequence[ConfigEnvValue] = (),
        focus_keys: Optional[set] = None,
        allow_id_reference: bool = True,
    ) -> List[str]:
        """把一个等级下的配置节点压缩成可读的键值片段。

        参数:
            - `entries`：属于同一等级的配置节点，类型为
              `Sequence[ConfigEntry]`，已按行号排序。
            - `level_position`：等级数字在当前完整路径中的位置，类型为 `int`；
              渲染时从该位置之后开始描述子路径。
            - `env_values`：同一等级的 `getByTestMode` 环境值，类型为
              `Sequence[ConfigEnvValue]`；门槛、价格常写在该函数里，必须与
              字面量节点合并展示。
            - `focus_keys`：问题点名的取值类别对应键集合，类型为
              `Optional[set]`；非空时只展示这些键，避免门槛问题被价格、折扣等
              无关字段淹没。
            - `allow_id_reference`：是否允许把 `id` 解析为同文件奖品定义，类型
              为 `bool`；只有名次表/奖品表为 `True`，等级记录自身的编号 `id`
              不解析，避免串入其他数组的奖励。

        Returns:
            按“名称、门槛、奖励、其他”优先级排列的键值片段。技术性字段
            （内部 id、资源 gid、每日步进、图片路径）和多语言重复名称不会
            输出；同一等级超过上限的键值会被截断并明确说明。
        """
        items: List[Tuple[int, int, str]] = []
        order = 0
        # 名称已经作为“等级 N（名称）”写在语句头部，这里不再重复输出
        # `name.zh/en/in`，避免同一名称出现三份。
        for entry in entries:
            if _is_name_like(entry):
                continue
            key = entry.key.casefold()
            tail = entry.path[level_position + 1:] or (entry.key,)
            label = ".".join(tail)
            # 等级记录自身的 `id` 只是编号，不允许跨数组解析；否则会把同文件
            # 其他数字键数组（例如 `magicAlchemy[3]`）误当成该等级的奖励。
            if key == "id" and not allow_id_reference:
                continue
            referenced = self._resolve_reference(entry)
            referenced_text = (
                self._referenced_text(referenced) if referenced else ""
            )
            if referenced_text:
                # 名次表的 `id` 与奖池的 `prizes` 都只是引用；解析出的奖品
                # 名称和数量比内部 id 更有价值。问题只问门槛时跳过奖励明细。
                if focus_keys is not None and not (focus_keys & _REWARD_FOCUS_KEYS):
                    continue
                items.append((1, order, f"奖励={referenced_text}"))
                order += 1
                continue
            if key in _HIDDEN_ENTRY_KEYS:
                continue
            if focus_keys is not None and key not in focus_keys:
                continue
            # `name.zh` 这类多语言子键已在名称合并中输出，避免重复。
            if len(tail) >= 2 and tail[0].casefold() in _NAME_GROUP_KEYS:
                continue
            if key in _LANGUAGE_KEYS:
                continue
            priority = _ENTRY_PRIORITY.get(key, 20)
            label = _ENTRY_LABELS.get(key, label)
            items.append((priority, order, f"{label}={entry.value_text}"))
            order += 1
        for item in env_values:
            key = item.key.casefold()
            if key in _HIDDEN_ENTRY_KEYS or key in _LANGUAGE_KEYS:
                continue
            if key in _NAME_GROUP_KEYS:
                continue
            if focus_keys is not None and key not in focus_keys:
                continue
            priority = _ENTRY_PRIORITY.get(key, 20)
            items.append((priority, order, _render_env_pair(item)))
            order += 1
        items.sort(key=lambda row: (row[0], row[1]))
        parts: List[str] = []
        for _, _, text in items[:_MAX_ENTRIES_PER_LEVEL]:
            # 相同文本（例如同一门槛在字面量和环境值里各出现一次）只展示一次。
            if text in parts:
                continue
            parts.append(text)
        if len(items) > _MAX_ENTRIES_PER_LEVEL:
            parts.append("其余键值未展开")
        return parts


def parse_config_graph(lines: Sequence[EvidenceLine]) -> ConfigGraph:
    """把配置源码证据解析成可查询的配置图。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。

    Returns:
        配置图对象。只有当证据中存在 PHP 数组配置行时才会产生节点；普通业务
        源码不会进入配置图，避免把类常量或方法参数误当配置值。
    """
    by_file: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        normalized = item.file.replace("\\", "/")
        if not normalized.endswith((".php", ".inc", ".conf", ".ini")):
            continue
        by_file.setdefault(item.file, []).append(item)
    entries: List[ConfigEntry] = []
    env_values: List[ConfigEnvValue] = []
    member_counts: Dict[Tuple[str, ...], int] = {}
    group_comments: Dict[Tuple[str, ...], str] = {}
    for file_name, file_lines in by_file.items():
        file_entries, file_counts, file_comments, file_line_paths = (
            _parse_file_entries(file_name, file_lines)
        )
        entries.extend(file_entries)
        for path, count in file_counts.items():
            # 计数键带上文件名，避免同名数组（例如多个活动的 `prizes`）互相覆盖。
            count_key = (file_name, *path)
            member_counts[count_key] = member_counts.get(count_key, 0) + count
        for path, comment in file_comments.items():
            group_comments.setdefault(path, comment)
        env_values.extend(
            _parse_env_values(file_name, file_lines, file_line_paths)
        )
    # 匿名列表（`'gifts' => [['id' => 6405], ...]`）在解析时逐元素记录字段，
    # 但“配置了几个指定礼物”需要的是列表成员总数。这里按直接子项的数字位置
    # 派生数量：只有从 1 开始连续编号才视为位置列表，显式业务键（prizes 的
    # 100/200…）不会被误判成成员数量。
    by_file_entries: Dict[str, List[ConfigEntry]] = {}
    for entry in entries:
        by_file_entries.setdefault(entry.file, []).append(entry)
    for file_name, file_entries in by_file_entries.items():
        positional: Dict[Tuple[str, ...], set[int]] = {}
        for entry in file_entries:
            for index, segment in enumerate(entry.path):
                if not segment.isdigit() or index == 0:
                    continue
                positional.setdefault(entry.path[:index], set()).add(int(segment))
        for prefix, indexes in positional.items():
            if prefix and prefix[-1].isdigit():
                continue
            ordered = sorted(indexes)
            if ordered and ordered[0] == 1 and ordered == list(range(1, len(ordered) + 1)):
                count_key = (file_name, *prefix)
                existing = member_counts.get(count_key)
                if existing is None or len(ordered) > existing:
                    member_counts[count_key] = len(ordered)
    entries.sort(key=lambda item: (item.file, item.line, item.path))
    env_values.sort(key=lambda item: (item.file, item.line, item.path))
    return ConfigGraph(entries, env_values, member_counts, group_comments)


def _scoped_config_evidence(
    lines: Sequence[EvidenceLine],
    question: str,
    scope_terms: Sequence[str] = (),
    include_standalone_configs: bool = False,
) -> Optional[List[EvidenceLine]]:
    """按活动范围筛选可用于配置回答的证据行。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`。
        - `scope_terms`：运行时术语扩展校验后的真实代码词，类型为
          `Sequence[str]`。
        - `include_standalone_configs`：是否把没有活动目录的传统全局配置
          （`php/config/noble.php`）也作为候选分组，类型为 `bool`。等级枚举
          打开该开关，才能读到贵族等级门槛。

    Returns:
        限定到单个活动后的配置证据行；具体选择规则由
        `analyzers.configuration.scoped_config_evidence` 提供，配置图只复用该
        通用逻辑，避免两套活动定位代码产生不同答案。
    """
    return scoped_config_evidence(
        lines,
        question,
        scope_terms,
        include_standalone_configs=include_standalone_configs,
    )


def configuration_enumeration_facts(
    lines: Sequence[EvidenceLine],
    question: str,
    scope_terms: Sequence[str] = (),
) -> List[Dict[str, object]]:
    """按等级/档位问题从配置证据中生成通用枚举事实。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`。
        - `scope_terms`：运行时术语扩展校验后的真实代码词，类型为
          `Sequence[str]`；用于把中文活动名定位到真实英文目录。

    Returns:
        逐等级配置事实。配置证据跨多个活动目录且问题无法用活动标识消歧时返回
        空列表，避免把“游戏榜”的问题答成另一个活动的配置。
    """
    scope = _scope_identifier(question, lines)
    # 只有“等级/档位/级别/阶段”这类逐级问题才需要传统全局配置
    # （`php/config/noble.php`）参与候选；普通计数问题仍沿用默认范围，
    # 避免全局配置文件抢走领域分析器的既有答案。
    # VIP 问题即使没写“等级”二字（“VIP 3 的门槛”）也属于逐级门槛查询，
    # 必须让没有活动目录的 `php/config/noble.php` 参与候选。
    level_intent = any(
        token in question.casefold()
        for token in ("等级", "档位", "档", "级别", "阶段", "vip")
    )
    scoped_lines = _scoped_config_evidence(
        lines,
        question,
        scope_terms,
        include_standalone_configs=level_intent,
    )
    if not scoped_lines:
        return []
    config_lines = _scoped_config_lines(scoped_lines, scope)
    return parse_config_graph(config_lines).enumeration_facts(question)


def configuration_env_value_facts(
    lines: Sequence[EvidenceLine],
    question: str,
    scope_terms: Sequence[str] = (),
) -> List[Dict[str, object]]:
    """按问题语义返回测试/正式环境配置值事实。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`。
        - `scope_terms`：运行时术语扩展校验后的真实代码词，类型为
          `Sequence[str]`；用于多活动证据中定位目标活动。

    Returns:
        环境配置事实列表。用于回答商户号、开关、默认地址等“测试是什么、
        正式是什么”的问题；敏感字段和地址值会脱敏展示。
    """
    scope = _scope_identifier(question, lines)
    scoped_lines = _scoped_config_evidence(lines, question, scope_terms)
    # 环境配置值常见于传统目录（`myapp/config/fupay.php`），这类布局没有活动
    # 目录标记，活动筛选会返回空；环境取值仍应读取这些配置文件，因为
    # `getByTestMode` 本身就是环境分支定义。
    if scoped_lines is None:
        scoped_lines = list(lines)
    if not scoped_lines:
        return []
    config_lines = _scoped_config_lines(scoped_lines, scope)
    return parse_config_graph(config_lines).env_value_facts(question)


def configuration_enumeration_conclusion(
    facts: Sequence[Dict[str, object]],
) -> str:
    """把逐等级配置事实压缩为直接回答。

    参数:
        - `facts`：`configuration_enumeration_facts` 生成的逐等级事实，类型为
          `Sequence[Dict[str, object]]`。

    Returns:
        按等级顺序拼接的结论。只复述配置字面值，不补充证据外的业务解释；
        没有可用事实时返回空字符串。
    """
    statements = [
        str(item.get("statement") or "").strip()
        for item in facts
        if str(item.get("statement") or "").strip()
    ]
    if not statements:
        return ""
    if all(str(item.get("category") or "") == "配置计数" for item in facts):
        return "已从配置源码统计：" + "；".join(statements) + "以上数量按数组成员计数得出。"
    # 名次表的数字键语义是“名次”，不能再用“等级”措辞，避免用户把名次
    # 奖励误读成等级门槛。
    ordinal_noun = (
        "名次" if all(statement.startswith("名次") for statement in statements)
        else "等级"
    )
    prefix = (
        f"已从配置源码按{ordinal_noun}确认（共 {len(statements)} 个{ordinal_noun}）："
        if len(statements) >= 2
        else f"已从配置源码按{ordinal_noun}确认："
    )
    # 每个等级单独成行，避免十多个等级被拼成一整段难以阅读的长句。
    bullet_list = "\n".join(
        f"{index}. {statement}"
        for index, statement in enumerate(statements, start=1)
    )
    return (
        prefix
        + "\n"
        + bullet_list
        + "\n以上取值直接来自当前证据窗口中的配置字面量。"
    )


def configuration_env_value_conclusion(
    facts: Sequence[Dict[str, object]],
) -> str:
    """把环境配置事实压缩为直接回答。

    参数:
        - `facts`：`configuration_env_value_facts` 生成的环境配置事实，类型为
          `Sequence[Dict[str, object]]`。

    Returns:
        逐条列出测试值和正式值的结论。敏感项和地址项只出现“已配置”，不会在
        结论中补充证据外的解密结果。
    """
    statements = [
        str(item.get("statement") or "").strip()
        for item in facts
        if str(item.get("statement") or "").strip()
    ]
    if not statements:
        return ""
    return (
        "已从配置源码确认测试/正式环境取值："
        + "；".join(statements)
        + "以上分支由 `getByTestMode` 的测试值和正式值参数决定。"
    )


__all__ = [
    "ConfigEntry",
    "ConfigEnvValue",
    "ConfigGraph",
    "configuration_enumeration_conclusion",
    "configuration_enumeration_facts",
    "configuration_env_value_conclusion",
    "configuration_env_value_facts",
    "parse_config_graph",
]
