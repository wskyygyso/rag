"""确定性分析器共享的源码证据与查找工具。

该模块只解释已经检索并带真实行号的源码，不访问仓库、数据库、Redis 或模型。
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class EvidenceLine:
    """一条带稳定文件行号的源码证据。

    `file` 是项目内相对路径，`line` 是 1 开始的行号，`content` 是该行原始
    文本，`ref` 是报告和 LLM 共用的 `文件:行号` 引用。
    """

    file: str
    line: int
    content: str
    ref: str

_CONTEXT_LINE_PATTERN = re.compile(r"^\s*(\d+):\s?(.*)$")

def _evidence_lines(evidence: Iterable[Dict[str, Any]]) -> List[EvidenceLine]:
    """把 Worker 保存的上下文片段展开为可精确引用的源码行。

    参数:
        - `evidence`：代码检索证据集合，每项至少包含 `file`、命中行和带行号的
          `context`，类型为 `Iterable[Dict[str, Any]]`。

    Returns:
        去重后的 `EvidenceLine` 列表。若旧任务没有 `context`，退回使用证据行号
        和 `content`，保证历史报告仍可分析。
    """
    lines: Dict[Tuple[str, int], EvidenceLine] = {}
    for item in evidence:
        file_name = str(item.get("file") or "")
        if not file_name:
            continue
        context = str(item.get("context") or "")
        parsed_any = False
        for raw_line in context.splitlines():
            match = _CONTEXT_LINE_PATTERN.match(raw_line)
            if not match:
                continue
            line_number = int(match.group(1))
            content = match.group(2)
            parsed_any = True
            lines.setdefault(
                (file_name, line_number),
                EvidenceLine(
                    file=file_name,
                    line=line_number,
                    content=content,
                    ref=f"{file_name}:{line_number}",
                ),
            )
        if parsed_any:
            continue
        line_number = int(item.get("line") or 0)
        if line_number > 0:
            content = str(item.get("content") or "").strip()
            lines.setdefault(
                (file_name, line_number),
                EvidenceLine(
                    file=file_name,
                    line=line_number,
                    content=content,
                    ref=f"{file_name}:{line_number}",
                ),
            )
    return sorted(lines.values(), key=lambda item: (item.file, item.line))

def _find_line(
    lines: Sequence[EvidenceLine],
    *,
    file_suffix: str = "",
    contains: Sequence[str] = (),
    contains_any: Sequence[str] = (),
) -> Optional[EvidenceLine]:
    """按文件后缀和源码文本查找第一条确定性证据行。

    参数:
        - `lines`：展开后的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `file_suffix`：目标文件路径后缀，例如 `UsdFuseService.php`，空字符串
          表示不限制文件，类型为 `str`。
        - `contains`：候选行必须同时包含的文本，类型为 `Sequence[str]`。
        - `contains_any`：候选行至少包含其中一项的文本，类型为 `Sequence[str]`。

    Returns:
        第一条匹配证据；没有命中时返回 `None`。匹配不区分英文大小写，避免
        PHP 类名与检索词大小写不同造成漏报。
    """
    required = tuple(item.casefold() for item in contains)
    alternatives = tuple(item.casefold() for item in contains_any)
    for item in lines:
        if file_suffix and not item.file.endswith(file_suffix):
            continue
        content = item.content.casefold()
        if required and not all(value in content for value in required):
            continue
        if alternatives and not any(value in content for value in alternatives):
            continue
        return item
    return None

def _find_line_after(
    lines: Sequence[EvidenceLine],
    anchor: Optional[EvidenceLine],
    needle: str,
    max_distance: int = 2,
) -> Optional[EvidenceLine]:
    """查找条件语句之后紧邻的提前返回或跳过动作。

    参数:
        - `lines`：展开后的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `anchor`：已经命中的条件行；为空时表示无法证明后续动作，类型为
          `Optional[EvidenceLine]`。
        - `needle`：后续动作行需要包含的文本，例如 `continue` 或 `return []`，
          类型为 `str`。
        - `max_distance`：允许跨过的最大行数，类型为 `int`。

    Returns:
        同一文件中紧跟条件的最长 `max_distance` 行内第一条动作证据；未找到时
        返回 `None`，避免把相似分支的动作错误归到当前条件。
    """
    if anchor is None:
        return None
    lowered_needle = needle.casefold()
    for item in lines:
        if item.file != anchor.file:
            continue
        if anchor.line < item.line <= anchor.line + max_distance:
            if lowered_needle in item.content.casefold():
                return item
    return None

def _config_integer(line: Optional[EvidenceLine], key: str) -> Optional[int]:
    """从配置证据行提取指定键后面的整数字面量。

    参数:
        - `line`：可能包含 PHP 配置项的源码证据，类型为 `Optional[EvidenceLine]`。
        - `key`：需要读取的配置键，例如 `min_diamonds`，类型为 `str`。

    Returns:
        配置字面量转换后的整数；证据行不存在、值来自函数调用或不是十进制整数
        时返回 `None`，避免把表达式误读为固定业务值。
    """
    if line is None:
        return None
    match = re.search(
        rf"['\"]?{re.escape(key)}['\"]?\s*(?:=>|=)\s*(\d+)(?:\s*[,;)]|$)",
        line.content,
    )
    return int(match.group(1)) if match else None

def _config_number(line: Optional[EvidenceLine], key: str) -> Optional[float]:
    """从渠道配置证据行提取指定键后面的十进制数值。

    参数:
        - `line`：可能包含渠道计费项的源码证据，类型为 `Optional[EvidenceLine]`。
        - `key`：需要读取的计费键，例如 `pay_fee`，类型为 `str`。

    Returns:
        比例费率或固定费的数值；没有配置字面量时返回 `None`，由调用方明确
        标记该渠道费率未确认。
    """
    if line is None:
        return None
    match = re.search(
        rf"['\"]?{re.escape(key)}['\"]?\s*(?:=>|=)\s*(\d+(?:\.\d+)?)",
        line.content,
    )
    return float(match.group(1)) if match else None

def _test_mode_integer_pair(line: Optional[EvidenceLine]) -> Optional[Tuple[int, int]]:
    """读取 `getByTestMode(测试值, 正式值)` 中的两个整数。

    参数:
        - `line`：官方提现下限等配置行，类型为 `Optional[EvidenceLine]`。

    Returns:
        `(测试环境值, 正式环境值)`；配置不是该函数调用时返回 `None`。两项的
        环境顺序随后还要由 `getByTestMode` 的实现证据共同确认。
    """
    if line is None:
        return None
    match = re.search(r"getByTestMode\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", line.content)
    return (int(match.group(1)), int(match.group(2))) if match else None

def _test_mode_number(line: Optional[EvidenceLine], key: str) -> Optional[float]:
    """读取指定配置键在 `getByTestMode()` 中的第一个数值。

    参数:
        - `line`：可能包含 PHP 配置项的源码证据，类型为 `Optional[EvidenceLine]`。
        - `key`：例如 `diamond_to_usd` 的配置键名，类型为 `str`。

    Returns:
        测试环境分支中的数值；当前仓库对该比例使用相同的测试和生产值，因此
        该数值也代表当前配置口径。配置表达式不存在时返回 `None`，不猜测缺省值。
    """
    if line is None:
        return None
    match = re.search(
        rf"['\"]?{re.escape(key)}['\"]?\s*(?:=>|=)\s*getByTestMode\(\s*(\d+(?:\.\d+)?)",
        line.content,
    )
    return float(match.group(1)) if match else None

def _lines_by_file(lines: Sequence[EvidenceLine]) -> Dict[str, List[EvidenceLine]]:
    """按项目内文件分组已排序的源码证据行。

    参数:
        - `lines`：从诊断证据上下文展开并去重后的源码行，类型为
          `Sequence[EvidenceLine]`。

    Returns:
        `文件路径 -> 按行号升序排列的证据` 映射。分组只在内存中进行，不会读取
        业务仓库的其他文件，避免分析器绕过已审计的证据范围。
    """
    grouped: Dict[str, List[EvidenceLine]] = {}
    for item in lines:
        grouped.setdefault(item.file, []).append(item)
    for items in grouped.values():
        items.sort(key=lambda evidence: evidence.line)
    return grouped
