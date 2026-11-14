"""问题类型路由。

该模块只根据问题文本和通用业务概念判断“应该用哪一类解释器”，不保存具体活动、
类名或答案映射。配置类问题统一交给配置图/配置取值解释器，流程和调用链问题继续
复用通用控制点与显式符号分析，因此新增业务名或新活动通常不再需要新增代码。

路由只决定分析策略，不生成业务事实；所有结论仍必须回到已检索源码证据。
"""

from enum import Enum
import re
from typing import Optional, Set

from app.repository_history import is_history_question


class QuestionType(str, Enum):
    """描述一次诊断应使用的解释器类型。"""

    CONFIG_ENUMERATION = "config_enumeration"
    """按等级/档位等重复结构逐项列出配置。"""

    CONFIG_LOOKUP = "config_lookup"
    """读取单个或一组配置项的取值/开关。"""

    FLOW_CONTROL = "flow_control"
    """需要解释条件、校验、拦截或计算流程。"""

    CALL_CHAIN = "call_chain"
    """问题显式指向类、方法或调用入口。"""

    FRONTEND_DISPLAY = "frontend_display"
    """需要解释页面展示条件和返回字段。"""

    HISTORY = "history"
    """需要查询功能引入版本或时间线。"""

    UNSUPPORTED = "unsupported"
    """当前没有可靠解释器，只能报告证据缺口。"""


# 枚举动作词：与“等级/档位/奖励”等业务对象组合时，表示要逐项列出。
_ENUMERATION_WORDS = (
    "每个",
    "每一",
    "所有",
    "全部",
    "列出",
    "分别",
    "各等级",
    "各档",
    "各档位",
    "逐级",
    "逐档",
    "一一列",
    "各自",
    "各自奖励",
    "哪个",
    "哪一个",
    "哪几个",
    "几档",
    "几档位",
    "前三名",
    "前几名",
    "日榜",
    "排名",
    "哪些",
    "哪几",
    "几级",
    "几轮",
    "几个",
    "几种",
    "几支",
    "几面",
    "多少个",
    "多少次",
)
# 配置对象词：说明问题关注的是配置值，而不是业务执行流程。
_CONFIG_OBJECT_WORDS = (
    "配置",
    "参数",
    "阈值",
    "上限",
    "下限",
    "门槛",
    "开关",
    "默认值",
    "值",
    "档",
    "档位",
    "比例",
    "费率",
    "倍率",
    "单价",
    "奖池",
    "规则",
    "阶段",
    "奖励",
    "奖品",
    "库存",
    "名称",
    "起止时间",
    # 活动配置里常见的业务数值对象。加入后，“多少燃料/多少金币/几个阶段”
    # 这类没有写“配置”二字的问题也能进入配置取值解释器。
    "燃料",
    "碎片",
    "宝箱",
    "类型",
    "期数",
    "金币",
    "钻石",
    "魔法石",
    "礼物",
    "时间",
    "数量",
    "目标值",
    "投注",
    "步长",
    "保底",
    "限额",
    "限制",
    "机制",
    "区间",
    "价格",
    "美元",
    "表",
    "结算",
    "截止",
    "秒",
)
# 取值动作词：与配置对象词共同出现时，进入配置取值解释器。
_LOOKUP_WORDS = (
    "多少",
    "是多少",
    "是什么",
    "分别是什么",
    "什么值",
    "取值",
    "值为",
    "设为",
    "配置为",
    "是否开启",
    "有没有",
    "有没有开启",
    "是否有",
    "有吗",
    "支持吗",
    "打开了吗",
    "怎么写的",
    "怎么配置",
    "如何配置",
    "配置在哪",
    "从哪里读取",
    "写法",
    # “按什么规则投入、怎么配、等于多少”同样是取值类问法，只是没有出现
    # “是多少”字样；只在同时出现配置对象词时才会进入配置取值解释器。
    "按什么",
    "什么规则",
    "怎么配",
    "等于多少",
    "吗",
)
# 等级/档位等重复结构词：即使问题没有写“配置”，也允许进入配置枚举。
_REPEATED_STRUCTURE_WORDS = (
    "等级",
    "档位",
    "级别",
    "阶梯",
    "奖励",
    "奖品",
    "礼包",
    "阶段",
    "轮",
    "排名",
    "名次",
    "前三",
    "日榜",
    "兜底",
)
_FRONTEND_WORDS = ("前端", "页面", "展示", "显示", "客户端", "钱包页")
_FLOW_WORDS = (
    "流程",
    "条件",
    "校验",
    "拦截",
    "触发",
    "计算",
    "分配",
    "发放",
    "判断",
    "限制",
    "熔断",
)
_CALL_CHAIN_WORDS = ("调用", "链路", "入口", "方法", "函数", "实现")
_EXPLICIT_CLASS_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9_]{2,})(?:::[A-Za-z_][A-Za-z0-9_]*)?\b"
)
# 常见技术缩写不是业务类名，避免把 `Redis key 怎么清理` 当成显式类问题。
_COMMON_TECH_TOKENS = {
    "API",
    "HTTP",
    "HTTPS",
    "ID",
    "JSON",
    "PHP",
    "REDIS",
    "SQL",
    "URL",
    "UUID",
    "YAML",
}


def _has_config_object(question: str, concepts: Set[str]) -> bool:
    """判断问题是否在询问配置对象。

    参数:
        - `question`：用户原始问题，类型为 `str`。
        - `concepts`：问题识别出的可组合业务概念名，类型为 `Set[str]`。

    Returns:
        问题包含配置对象词、显式配置概念或点号/下划线配置键时返回 `True`。
    """
    if "config" in concepts:
        return True
    if any(word in question for word in _CONFIG_OBJECT_WORDS):
        return True
    if re.search(r"`[A-Za-z_][A-Za-z0-9_.]{2,}`", question):
        return True
    # 未加反引号的点号配置路径（`pool.base`）同样是用户在点名配置键，不能
    # 因为缺少引号就退回流程分析器。
    return bool(
        re.search(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*", question)
    )


def _has_explicit_class(question: str) -> bool:
    """判断问题是否显式给出了可用源码文件验证的类名。

    参数:
        - `question`：用户原始问题，类型为 `str`。

    Returns:
        存在非技术缩写、首字母大写的类名或 `Class::method` 形式标识时返回
        `True`；纯大写缩写和普通英文单词不会触发。
    """
    for match in _EXPLICIT_CLASS_PATTERN.finditer(question):
        name = match.group(1)
        if name.upper() in _COMMON_TECH_TOKENS or name.isupper():
            continue
        return True
    return False


def asks_member_count(question: str) -> bool:
    """判断问题是否在询问“有多少个成员”而不是某个具体等级值。

    参数:
        - `question`：用户原始问题，类型为 `str`。

    Returns:
        出现“多少支/多少种/几支/几种/多少个/哪些”等数量或列举词时返回
        `True`。调用方据此允许配置图尝试数组成员计数，用于回答国旗数量、
        监听类型数量等没有等级键的问题。
    """
    return any(
        token in question
        for token in ("多少支", "多少种", "多少面", "多少个", "多少款", "几支", "几种", "几面", "哪些")
    )


def classify_question(
    question: str,
    concepts: Optional[Set[str]] = None,
) -> QuestionType:
    """根据通用语义特征选择问题解释器类型。

    参数:
        - `question`：用户原始业务问题，类型为 `str`；判断只使用可复用的动作词、
          对象词和源码标识，不依赖项目名或完整问句模板。
        - `concepts`：`detect_semantic_concepts` 识别出的概念名集合，类型为
          `Optional[Set[str]]`；为空时按空集合处理。

    返回:
        路由到的 `QuestionType`。优先级为历史查询、配置枚举、前端展示、配置取值、
        显式调用链、流程控制；没有可识别特征时返回 `UNSUPPORTED`，由上层明确
        报告证据缺口，而不是用泛化词拼凑结论。
    """
    normalized_concepts = concepts or set()
    folded = (question or "").casefold()
    if is_history_question(question or ""):
        return QuestionType.HISTORY
    enumeration = any(word in question for word in _ENUMERATION_WORDS)
    repeated_structure = any(
        word in question for word in _REPEATED_STRUCTURE_WORDS
    )
    # “第 2 档/第 3 名/第 2 级”说明用户在点名单个等级或名次。枚举解释器
    # 会按序号收窄到对应条目，因此这里和“分别/哪些”一样视为重复结构问题。
    ordinal_structure = bool(
        re.search(r"第\s*[\d一二三四五六七八九十]+\s*(?:个|档|级|名|项|轮)", question)
    )
    if ordinal_structure and _has_config_object(question, normalized_concepts):
        return QuestionType.CONFIG_ENUMERATION
    if enumeration and (
        _has_config_object(question, normalized_concepts) or repeated_structure
    ):
        return QuestionType.CONFIG_ENUMERATION
    if any(word in question for word in _FRONTEND_WORDS) or (
        "display" in normalized_concepts and "frontend" in normalized_concepts
    ):
        return QuestionType.FRONTEND_DISPLAY
    if _has_config_object(question, normalized_concepts) and any(
        word in question for word in _LOOKUP_WORDS
    ):
        return QuestionType.CONFIG_LOOKUP
    if _has_explicit_class(question) or any(
        word in question for word in _CALL_CHAIN_WORDS
    ):
        return QuestionType.CALL_CHAIN
    if any(word in folded for word in _FLOW_WORDS) or normalized_concepts.intersection(
        {"risk", "limit", "preview", "flow"}
    ):
        return QuestionType.FLOW_CONTROL
    return QuestionType.UNSUPPORTED


__all__ = ["QuestionType", "classify_question"]
