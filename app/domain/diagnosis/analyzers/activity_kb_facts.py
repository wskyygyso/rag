"""已链接活动的结构化事实：期次、等级表、奖励引用与后台活动登记。

模块职责：
- 从 `retrieval_plan.activity_link` 的结构化快照生成带源码引用的确定性事实；
- 只服务“已链接到唯一活动”的问题，未链接或链接歧义时一律返回空列表；
- 为等级表兜底问答提供可读名称、门槛与奖励名称。

边界与安全约束：
- 纯函数：不读取文件、不访问数据库或模型，所有数据来自知识库已解析的配置字面值；
- 不生成活动名映射、不补猜缺失字段；没有名称的奖励直接跳过，避免倾倒内部 id；
- 事实只引用 `文件:行号`，不输出密钥、URL、图片地址或完整源码。
"""

import re
from typing import Any, Dict, List, Optional, Sequence

# 期次意图：显式第几期、期次/期号，或用户直接点名 `vol` 配置键。
_PERIOD_PATTERN = re.compile(
    r"第\s*[\d一二三四五六七八九十]+\s*期|期次|期号|第几期|\bvol\b",
    re.IGNORECASE,
)
# 后台活动登记意图。命中后才输出后台登记事实，普通配置问题不受影响。
_BACKEND_TOKENS = ("后台", "登记")
# 等级表意图：与现有配置枚举的等级/档位问法保持一致。
_LEVEL_INTENT_TOKENS = ("等级", "档位", "级别", "阶段", "门槛", "level")
# 用户点名“第 3 档/第 3 级/第 3 个等级”或“等级 3/档位 3”时只输出该条。
_LEVEL_ORDINAL_PATTERN = re.compile(
    r"第\s*(\d+)\s*(?:档|级|等级|阶段)|(?:等级|档位|级别|阶段)\s*(\d+)"
)
# 单次结论最多输出的等级条数，避免异常等级表撑长回复。
MAX_FACT_LEVELS = 20
# 单条事实最多保留的证据引用条数。
MAX_FACT_REFS = 6


def _linked_snapshot(activity_link: Any) -> Optional[Dict[str, Any]]:
    """校验链接摘要并返回可用于生成事实快照的字典。

    参数:
        - `activity_link`：工作流写入 `retrieval_plan.activity_link` 的摘要，
          类型为 `Any`；正常情况是 `Dict[str, Any]`。

    Returns:
        可用快照字典；知识库不可用、未链接、链接歧义或缺少活动名时返回
        `None`。调用方必须把 `None` 视为“不得使用该活动事实”。
    """
    if not isinstance(activity_link, dict):
        return None
    if not activity_link.get("available"):
        return None
    if activity_link.get("ambiguous"):
        return None
    if not str(activity_link.get("activity_id") or "").strip():
        return None
    return activity_link


def _fact_refs(values: Sequence[Any]) -> List[str]:
    """把证据引用去空、去重并截断。

    参数:
        - `values`：`文件:行号` 字符串或空值序列，类型为 `Sequence[Any]`。

    Returns:
        去重后的非空引用列表，最多 `MAX_FACT_REFS` 条；全部为空时返回空列表，
        由调用方继续输出无引用事实或跳过该事实。
    """
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value or "").strip()
        )
    )[:MAX_FACT_REFS]


def _question_level_ordinal(question: str) -> Optional[int]:
    """读取问题中点名的等级序号。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。

    Returns:
        问题出现“第 N 档/级/等级/阶段”或“等级 N/档位 N”时返回 `N`；没有点名
        序号时返回 `None`。只接受阿拉伯数字，避免把“第一档”误解析成错误序号。
    """
    match = _LEVEL_ORDINAL_PATTERN.search(question or "")
    if not match:
        return None
    value = match.group(1) or match.group(2) or ""
    return int(value) if value else None


def activity_structured_facts(
    activity_link: Any,
    question: str,
) -> List[Dict[str, Any]]:
    """生成已链接活动的期次与后台活动登记事实。

    参数:
        - `activity_link`：实体链接摘要，类型为 `Any`；需提供 `activity_id`、
          `volume`、`year`、`vol_ref`、`year_ref`、`backend_registered` 和
          `backend_config_file`。
        - `question`：用户原始业务问题，类型为 `str`；只有命中期次或后台登记
          意图时才生成事实。

    Returns:
        最多两条事实：`category` 为 `活动期次` 或 `后台活动登记`，`statement`
        为可直接展示的中文结论，`evidence_refs` 为 `文件:行号`。未链接、歧义、
        或问题不含对应意图时返回空列表，由上层继续使用其他分析器。
    """
    snapshot = _linked_snapshot(activity_link)
    if snapshot is None:
        return []
    question_text = question or ""
    activity_id = str(snapshot.get("activity_id") or "").strip()
    facts: List[Dict[str, Any]] = []
    if _PERIOD_PATTERN.search(question_text):
        volume = str(snapshot.get("volume") or "").strip()
        if volume:
            year = str(snapshot.get("year") or "").strip()
            year_part = f"，配置年份 {year}" if year else ""
            facts.append(
                {
                    "category": "活动期次",
                    "statement": (
                        f"活动「{activity_id}」为第 {volume} 期"
                        f"（配置 vol={volume}{year_part}）"
                    ),
                    "evidence_refs": _fact_refs(
                        [snapshot.get("vol_ref"), snapshot.get("year_ref")]
                    ),
                }
            )
    if any(token in question_text for token in _BACKEND_TOKENS):
        registered = bool(snapshot.get("backend_registered"))
        backend_file = str(snapshot.get("backend_config_file") or "").strip()
        config_file = str(snapshot.get("config_file") or "").strip()
        if registered and backend_file:
            statement = (
                f"该活动已在后台活动登记中：后台活动名为「{activity_id}」，"
                f"后台读取配置 {backend_file}"
            )
            refs = _fact_refs([f"{backend_file}:1"])
        else:
            statement = (
                f"该活动不在后台活动登记路径（crontab/{activity_id}/config.php "
                f"或 Logic/{activity_id}/boot/config.php）中"
            )
            refs = _fact_refs([f"{config_file}:1"])
        facts.append(
            {
                "category": "后台活动登记",
                "statement": statement,
                "evidence_refs": refs,
            }
        )
    return facts


def activity_level_table_facts(
    activity_link: Any,
    question: str,
) -> List[Dict[str, Any]]:
    """把已链接活动的等级表渲染为逐级确定性事实。

    参数:
        - `activity_link`：实体链接摘要，类型为 `Any`；需提供 `levels`
          （含 `ordinal`/`threshold`/`name`/`prize_ids`/`ref`）和 `prizes`
          （含 `id`/`name`/`ref`）。
        - `question`：用户原始业务问题，类型为 `str`；必须含等级/档位/门槛
          意图，点名“第 N 档”时只输出该条。

    Returns:
        每个等级一条事实，最多 `MAX_FACT_LEVELS` 条。名称、门槛、奖励名称都来自
        知识库快照；门槛与奖励名都缺失的等级跳过，无名奖励不展示内部 id。
        未链接、歧义、没有等级表或问题无等级意图时返回空列表。
    """
    snapshot = _linked_snapshot(activity_link)
    if snapshot is None:
        return []
    folded_question = (question or "").casefold()
    if not any(token in folded_question for token in _LEVEL_INTENT_TOKENS):
        return []
    levels = snapshot.get("levels") or []
    if not isinstance(levels, list) or not levels:
        return []
    prize_map: Dict[str, Dict[str, Any]] = {}
    for prize in snapshot.get("prizes") or []:
        if not isinstance(prize, dict):
            continue
        prize_id = str(prize.get("id") or "").strip()
        if prize_id:
            prize_map[prize_id] = prize
    ordinal = _question_level_ordinal(question)
    facts: List[Dict[str, Any]] = []
    for level in levels[:MAX_FACT_LEVELS]:
        if not isinstance(level, dict):
            continue
        try:
            number = int(level.get("ordinal") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        if ordinal is not None and number != ordinal:
            continue
        name = str(level.get("name") or "").strip()
        threshold = str(level.get("threshold") or "").strip()
        prize_names: List[str] = []
        prize_refs: List[str] = []
        for prize_id in level.get("prize_ids") or []:
            prize = prize_map.get(str(prize_id))
            if not prize:
                continue
            prize_name = str(prize.get("name") or "").strip()
            if prize_name:
                prize_names.append(prize_name)
            reference = str(prize.get("ref") or "").strip()
            if reference:
                prize_refs.append(reference)
        # 门槛和奖励都缺失时只剩一个等级编号，不构成可核对的业务事实。
        if not threshold and not prize_names:
            continue
        parts: List[str] = []
        if threshold:
            parts.append(f"门槛 {threshold}")
        if prize_names:
            parts.append("奖励 " + "、".join(dict.fromkeys(prize_names)))
        label = f"等级 {number}" + (f"（{name}）" if name else "")
        facts.append(
            {
                "category": label,
                "statement": f"{label}：" + "；".join(parts),
                "evidence_refs": _fact_refs([level.get("ref"), *prize_refs]),
            }
        )
    return facts
