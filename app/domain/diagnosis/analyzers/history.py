"""基于 Git 提交快照的通用功能历史事实分析器。

分析器只读取受控历史通道已经采集的提交元数据和源码快照，不访问 Git 命令，也不
依赖项目名或完整问句模板。它可以回答“可访问历史中的最早相关提交”和“该提交中的
版本门槛”，但不会把单个工作区仓库直接等同于线上首发时间。
"""

import re
from typing import Any, Dict, List, Sequence

from app.domain.diagnosis.analyzers.common import EvidenceLine


_VERSION_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?P<name>[A-Z][A-Z0-9_]*(?:VERSION|BUILD)|[A-Z][A-Z0-9_]*_VERSION)"
    r"\s*=\s*(?P<value>\d+)\b",
    re.IGNORECASE,
)

# 客户端常见版本编码会把版本字符串前两段拼接或转成整数。这里只识别源码中
# 可验证的“同一分段数组第 0、1 项组合”动作，并把数组变量名作为反向引用，
# 不绑定具体项目中的 `$arr`、`$parts` 或其他局部变量命名。
_VERSION_SEGMENT_CONCAT_PATTERN = re.compile(
    r"\bintval\s*\(\s*\$?(?P<segments>[A-Za-z_][A-Za-z0-9_]*)\s*\[\s*0\s*\]"
    r"\s*\.\s*\$?(?P=segments)\s*\[\s*1\s*\]\s*\)",
    re.IGNORECASE,
)


def _format_client_version(version: int, build: int) -> str:
    """把客户端整数版本编码格式化为常见的 `主版本.次版本.构建号`。

    参数:
        - `version`：客户端 `getAppVersion` 返回的大版本整数，例如 `204`。
        - `build`：同一大版本下的递增构建号，例如 `111`。

    Returns:
        展示用版本字符串。最后两位作为次版本并补零，其余部分作为主版本，因此
        `204 + 111` 会显示为 `2.04.111`；该格式仍由源码中的版本解析规则确认。
    """
    digits = str(max(0, version))
    if len(digits) <= 2:
        formatted_version = f"0.{int(digits):02d}"
    else:
        formatted_version = f"{digits[:-2]}.{digits[-2:]}"
    return f"{formatted_version}.{max(0, build)}"


def _commit_ref(event: Dict[str, Any], snapshot_line: Dict[str, Any]) -> str:
    """生成事实可引用、同时保留当前证据定位格式的源码行引用。

    参数:
        - `event`：包含提交哈希和仓库路径的历史事件。
        - `snapshot_line`：该提交中的源码快照行，包含项目内文件和真实行号。

    Returns:
        `项目内文件:行号` 引用。提交哈希保留在事实文本中，便于审阅人区分同一路径
        在不同历史版本中的内容。
    """
    return f"{snapshot_line.get('file')}:{snapshot_line.get('line')}"


def analyze_history_events(
    lines: Sequence[EvidenceLine],
    history_events: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """从最早相关提交和当前版本解析源码中生成功能历史事实。

    参数:
        - `lines`：当前工作区已经检索到的源码证据，类型为
          `Sequence[EvidenceLine]`，用于确认 `getAppVersion` 的编码规则。
        - `history_events`：Git 历史通道返回的提交事件，每项包含提交哈希、作者
          时间、提交说明、变更文件和源码快照。

    Returns:
        最多四条确定性事实：最早相关提交、版本门槛、版本编码规则和历史证据范围。
        没有提交事件时返回空列表；只有提交元数据时不会推测客户端版本号。
    """
    if not history_events:
        return []
    earliest = history_events[0]
    commit_hash = str(earliest.get("commit") or "")
    short_commit = str(earliest.get("short_commit") or commit_hash[:12])
    repository = str(earliest.get("repository") or ".")
    authored_at = str(earliest.get("authored_at") or "时间未知")
    subject = str(earliest.get("subject") or "未填写提交说明").strip()
    files = [str(value) for value in earliest.get("files") or [] if str(value)]
    snapshot_lines = [
        item
        for item in earliest.get("snapshot_lines") or []
        if item.get("file") and item.get("line")
    ]
    matching_lines = [item for item in snapshot_lines if item.get("is_match")]
    commit_refs = [
        _commit_ref(earliest, item)
        for item in (matching_lines or snapshot_lines)[:3]
    ]
    commit_refs = list(dict.fromkeys(commit_refs))
    facts: List[Dict[str, Any]] = [
        {
            "category": "历史首次出现",
            "statement": (
                f"在可访问的 Git 历史中，最早命中相关业务词的提交是"
                f" `{short_commit}`（{authored_at}，子仓库 `{repository}`，"
                f"说明：{subject}）。该提交变更了 {len(files)} 个源码或配置文件。"
            ),
            "evidence_refs": commit_refs,
        }
    ]

    version_candidates = {}
    for item in snapshot_lines:
        for match in _VERSION_ASSIGNMENT_PATTERN.finditer(str(item.get("content") or "")):
            name = match.group("name").upper()
            if name not in version_candidates:
                version_candidates[name] = {
                    "value": int(match.group("value")),
                    "ref": _commit_ref(earliest, item),
                }
    version = next(
        (
            int(item["value"])
            for name, item in version_candidates.items()
            if name.endswith("VERSION") and "MIN" in name
        ),
        None,
    )
    build = next(
        (
            int(item["value"])
            for name, item in version_candidates.items()
            if name.endswith("BUILD") and "MIN" in name
        ),
        None,
    )
    version_refs = [
        str(item["ref"])
        for item in version_candidates.values()
        if item.get("ref")
    ]
    if version is not None or build is not None:
        assignments = ", ".join(
            f"`{name}={item['value']}`"
            for name, item in sorted(version_candidates.items())
        )
        facts.append(
            {
                "category": "版本门槛",
                "statement": (
                    f"提交 `{short_commit}` 中的相关实现声明版本门槛：{assignments}。"
                ),
                "evidence_refs": list(dict.fromkeys(version_refs)),
            }
        )

    encoding_line = next(
        (
            item
            for item in lines
            if _VERSION_SEGMENT_CONCAT_PATTERN.search(item.content)
        ),
        None,
    )
    if version is not None and build is not None and encoding_line is not None:
        facts.append(
            {
                "category": "版本编码规则",
                "statement": (
                    f"项目将 Android 版本字符串的前两段拼接为大版本整数"
                    f"（`{encoding_line.content.strip()}`），因此 version={version}、"
                    f"build={build} 对应 Android 客户端 {_format_client_version(version, build)}。"
                ),
                "evidence_refs": [encoding_line.ref, *(version_refs[:1])],
            }
        )
    return facts[:4]


__all__ = ["analyze_history_events"]
