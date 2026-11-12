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
    _activity_root,
    _inline_comment,
    _lines_in_supported_config_dirs,
    _safe_literal_text,
    _scope_identifier,
    _scoped_config_lines,
)
from app.query_understanding import detect_semantic_concepts


# 字符串键数组：`'prize' => [`。
_STRING_GROUP_PATTERN = re.compile(
    r"^['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*\["
)
# 数字键数组：`1 => [` 或 `1 => array(`，用于等级、档位等重复块。
_NUMERIC_GROUP_PATTERN = re.compile(r"^(?P<key>\d+)\s*=>\s*(?:\[|array\s*\()")
# 数字键环境选择：`1 => getByTestMode(`，测试/正式分支由后续注释标记分隔。
_TEST_MODE_GROUP_PATTERN = re.compile(r"^(?P<key>\d+)\s*=>\s*getByTestMode\s*\(")
# 单行内可能同时出现多个键值对，使用 finditer 逐个提取。
_LITERAL_ENTRY_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*"
    r"(?P<value>-?\d+(?:\.\d+)?|true|false|'[^'\n]{1,80}')(?=\s*[,}\]])",
    re.IGNORECASE,
)
_CONSTANT_ENTRY_PATTERN = re.compile(
    r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)['\"]\s*=>\s*"
    r"(?P<value>\\?[A-Za-z_][A-Za-z0-9_\\]*::[A-Za-z_][A-Za-z0-9_]*)"
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
    """解析过程中尚未闭合的 PHP 数组层级。"""

    indent: int
    key: str
    test_mode: bool
    branch: str


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
) -> List[ConfigEntry]:
    """把单个配置文件证据解析为带路径的字面量节点。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。
        - `file_lines`：同一文件按行号排序的证据行，类型为
          `Sequence[EvidenceLine]`；证据窗口可能不完整，缺失层级只影响该分支的
          枚举完整性，不影响已读到的配置事实。

    Returns:
        去重后的配置节点列表。解析按缩进维护数组栈，并识别
        `N => getByTestMode(测试值, 正式值)` 的测试/正式分支。
    """
    stack: List[_OpenGroup] = []
    entries: List[ConfigEntry] = []
    seen = set()
    for item in sorted(file_lines, key=lambda row: row.line):
        content = item.content
        stripped = content.strip()
        if not stripped:
            continue
        indent = _line_indent(content)

        while stack and indent <= stack[-1].indent:
            stack.pop()

        # 正式分支标记通常单独成行，也可能跟在数组开头。必须先切换分支，
        # 再解析同一行后面的键值，否则正式配置会被误当成测试配置。
        if _PRODUCTION_BRANCH_MARKER in content and stack and stack[-1].test_mode:
            stack[-1].branch = _PRODUCTION_BRANCH

        group = (
            _TEST_MODE_GROUP_PATTERN.match(stripped)
            or _NUMERIC_GROUP_PATTERN.match(stripped)
            or _STRING_GROUP_PATTERN.match(stripped)
        )
        if group:
            is_test_mode = bool(_TEST_MODE_GROUP_PATTERN.match(stripped))
            stack.append(
                _OpenGroup(
                    indent=indent,
                    key=group.group("key"),
                    test_mode=is_test_mode,
                    branch=_TEST_BRANCH if is_test_mode else "",
                )
            )

        path = tuple(entry.key for entry in stack)
        branch = stack[-1].branch if stack else ""
        comment = _inline_comment(content)
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
            entry_path = (*path, key)
            signature = (item.file, item.line, entry_path, value_text, branch)
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
                    branch=branch,
                )
            )
    return entries


def _parse_env_values(
    file_name: str,
    file_lines: Sequence[EvidenceLine],
) -> List[ConfigEnvValue]:
    """解析单行 `getByTestMode(测试值, 正式值)` 配置。

    参数:
        - `file_name`：项目内相对路径，类型为 `str`。
        - `file_lines`：同一文件的证据行，类型为 `Sequence[EvidenceLine]`；只读取
          当前证据窗口，不访问窗口外源码。

    Returns:
        环境覆盖配置列表。多行 URL 配置会在第一行命中键名和函数名，但值无法
        完整解析时不生成事实，避免把半截地址写入报告。
    """
    values: List[ConfigEnvValue] = []
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
                    path=(key,),
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


class ConfigGraph:
    """保存一次诊断证据中的配置节点，并提供通用枚举查询。"""

    def __init__(
        self,
        entries: Sequence[ConfigEntry],
        env_values: Sequence[ConfigEnvValue] = (),
    ) -> None:
        """初始化配置图。

        参数:
            - `entries`：`parse_config_graph` 生成的配置节点，类型为
              `Sequence[ConfigEntry]`；只包含已通过敏感值过滤的字面量。
            - `env_values`：`getByTestMode` 环境覆盖值，类型为
              `Sequence[ConfigEnvValue]`；渲染时会再次做敏感字段脱敏。
        """
        self._entries = tuple(entries)
        self._env_values = tuple(env_values)
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
            `evidence_refs`。没有重复数字键分组、证据跨多个活动目录或分组与
            问题完全无关时返回空列表，由上层继续使用配置取值或其他分析器。
        """
        if not self._entries:
            return []
        anchors = [
            value
            for value in extract_generic_anchors(question, max_anchors=48)
            if len(value) >= 2
        ]
        concepts = {item.name for item in detect_semantic_concepts(question)}
        candidates = self._enumeration_candidates(anchors, concepts)
        if not candidates:
            return []
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
    ) -> List[_EnumerationCandidate]:
        """发现所有可能的重复数字键分组并按问题相关度排序。

        参数:
            - `anchors`：问题提取出的中文业务片段，类型为 `Sequence[str]`。
            - `concepts`：问题识别出的可组合业务概念名，类型为 `set[str]`。

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
                        file_name, prefix, entries, anchors, concepts
                    )
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
                -len(item.levels),
                item.file,
                item.position,
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
    ) -> int:
        """计算一个等级分组与用户问题的相关度。

        参数:
            - `file_name`：配置文件相对路径，类型为 `str`。
            - `prefix`：等级数字键之前的数组路径，类型为 `Tuple[str, ...]`。
            - `entries`：该父路径下属于各个等级的配置节点，类型为
              `Sequence[ConfigEntry]`。
            - `anchors`：问题中的中文业务片段，类型为 `Sequence[str]`。
            - `concepts`：问题中的可组合业务概念名，类型为 `set[str]`。

        Returns:
            用于候选排序的非负分值。文件路径、数组路径、中文注释和源码内容与
            问题重合越多，分值越高；不包含项目名或活动名的硬编码权重。
        """
        context = " ".join(
            [
                file_name,
                " ".join(prefix),
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
        return score

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
            raw_level = entry.path[candidate.position]
            if not raw_level.isdigit():
                continue
            if entry.branch not in wanted_branches:
                continue
            by_level.setdefault(int(raw_level), []).append(entry)

        facts: List[Dict[str, object]] = []
        parent_label = candidate.prefix[-1] if candidate.prefix else "配置"
        for level in sorted(by_level)[:_MAX_ENUMERATED_LEVELS]:
            level_entries = sorted(by_level[level], key=lambda item: item.line)
            if not level_entries:
                continue
            parts = self._render_entries(level_entries, candidate.position)
            if not parts:
                continue
            branch_note = ""
            if branch_label:
                branches = {entry.branch for entry in level_entries if entry.branch}
                if len(branches) == 1:
                    branch_note = (
                        "（测试分支）"
                        if _TEST_BRANCH in branches
                        else "（正式分支）"
                    )
            facts.append(
                {
                    "category": f"{parent_label}等级{level}",
                    "statement": f"等级 {level}{branch_note}：" + "；".join(parts),
                    "evidence_refs": list(
                        dict.fromkeys(entry.ref for entry in level_entries)
                    ),
                }
            )
        return facts

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
            if common == 0 or common <= best_depth:
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
            形如 `name=100美元，type=dollar，num=0` 的说明。`name`、`title`
            等展示字段排在前面，`id` 作为引用本身不重复输出；最多保留六个
            字段，避免单条事实过长。
        """
        ordered = sorted(
            entries,
            key=lambda item: (
                0 if item.key in {"name", "title", "desc"} else 1,
                item.path,
            ),
        )
        parts = [
            f"{item.key}={item.value_text}"
            for item in ordered
            if item.key != "id"
        ]
        return "，".join(parts[:6])

    def _render_entries(
        self,
        entries: Sequence[ConfigEntry],
        level_position: int,
    ) -> List[str]:
        """把一个等级下的配置节点压缩成可读的键值片段。

        参数:
            - `entries`：属于同一等级的配置节点，类型为
              `Sequence[ConfigEntry]`，已按行号排序。
            - `level_position`：等级数字在当前完整路径中的位置，类型为 `int`；
              渲染时从该位置之后开始描述子路径。

        Returns:
            按源码顺序排列的键值片段。同一源码行内的多个键值对合并为一项；
            超过单等级上限的键值会被截断，并在末尾明确说明。
        """
        lines: Dict[int, List[ConfigEntry]] = {}
        for entry in entries:
            lines.setdefault(entry.line, []).append(entry)
        parts: List[str] = []
        rendered = 0
        for line_number in sorted(lines):
            line_entries = lines[line_number]
            tail_parts = []
            for entry in line_entries:
                tail = entry.path[level_position + 1:]
                label = ".".join(tail) if tail else entry.key
                referenced = self._resolve_reference(entry)
                referenced_text = (
                    self._referenced_text(referenced) if referenced else ""
                )
                if referenced_text:
                    tail_parts.append(
                        f"{label}={entry.value_text}（{referenced_text}）"
                    )
                else:
                    tail_parts.append(f"{label}={entry.value_text}")
                rendered += 1
            if tail_parts:
                parts.append("，".join(tail_parts))
            if rendered >= _MAX_ENTRIES_PER_LEVEL:
                parts.append("其余键值未展开")
                break
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
    for file_name, file_lines in by_file.items():
        entries.extend(_parse_file_entries(file_name, file_lines))
        env_values.extend(_parse_env_values(file_name, file_lines))
    entries.sort(key=lambda item: (item.file, item.line, item.path))
    env_values.sort(key=lambda item: (item.file, item.line, item.path))
    return ConfigGraph(entries, env_values)


def _scoped_config_evidence(
    lines: Sequence[EvidenceLine],
    question: str,
    scope_terms: Sequence[str] = (),
) -> Optional[List[EvidenceLine]]:
    """按活动范围筛选可用于配置回答的证据行。

    参数:
        - `lines`：当前诊断证据展开后的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`；用于提取活动路径标识。
        - `scope_terms`：运行时术语扩展校验后的真实代码词，类型为
          `Sequence[str]`；中文活动名无法直接匹配路径时，用这些真实英文词缩小
          到对应活动目录。

    Returns:
        限定到单个活动后的配置证据行；证据跨多个活动且无法用问题或扩展词消歧时
        返回 `None`，由调用方停止配置回答，避免把另一个活动的配置当成答案。
    """
    scope = _scope_identifier(question, lines)
    if scope:
        return lines
    normalized_terms = [
        re.sub(r"[^a-z0-9]", "", str(term).casefold())
        for term in scope_terms
        if len(str(term).strip()) >= 4
    ]
    normalized_terms = [term for term in normalized_terms if term]
    if normalized_terms:
        # 扩展词来自项目索引校验，是仓库中真实存在的英文标识符。优先选择
        # “足够长且覆盖多个真实文件”的词作为活动范围：`game_ranking` 会同时
        # 命中活动配置、Boot 和控制器，而更长的 `gameRankingControl` 只命中
        # 控制器一个文件，无法带出配置定义。
        matches: Dict[str, set] = {}
        for term in dict.fromkeys(normalized_terms):
            matched_files = {
                item.file
                for item in lines
                if term
                in re.sub(
                    r"[^a-z0-9]",
                    "",
                    item.file.replace("\\", "/").casefold(),
                )
            }
            if matched_files:
                matches[term] = matched_files
        if matches:
            best_term = max(
                matches,
                key=lambda term: (
                    len(term) * min(len(matches[term]), 3),
                    len(term),
                ),
            )
            selected_files = matches[best_term]
            return [item for item in lines if item.file in selected_files]
    scoped = _lines_in_supported_config_dirs(lines)
    if scoped:
        return scoped
    # 证据只包含一个活动目录时，即使同目录源码尚未进入窗口，也不存在跨活动
    # 串答风险。此时允许直接解析该配置，避免新活动因缺少 Boot/Logic 佐证而
    # 无法回答；多个活动目录仍必须保持空结果。
    roots = {_activity_root(item.file.replace("\\", "/")) for item in lines}
    if len({root for root in roots if root}) == 1:
        return lines
    return None


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
    scoped_lines = _scoped_config_evidence(lines, question, scope_terms)
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
    return (
        "已从配置源码按等级确认："
        + "；".join(statements)
        + "。以上取值直接来自当前证据窗口中的配置字面量。"
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
