"""通用业务查询理解和跨语言概念扩展。

本模块把中文问题拆成可组合的业务概念，再把概念扩展到常见英文代码词。它不保存
“某个完整问题对应某组类名、方法或表”的答案模板，因此新增问法通常只需要复用
已有概念；只有出现真正的新业务名词时才需要补充一个小词条，而不是维护整条调用链。

检索结果仍必须回到源码验证。这里生成的概念只用于发现候选文件和符号，不能直接
作为业务事实或诊断结论。
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class SemanticConcept:
    """描述一个可跨中英文组合的业务概念。

    `name` 是稳定的概念标识，`triggers` 是问题或注释中的中文及同义表达，
    `code_terms` 是代码命名中常见的英文词。概念之间可以自由组合，例如“钻石 +
    展示 + 档位”会生成三个独立概念，而不是绑定某个钱包接口的完整答案。
    """

    name: str
    triggers: Tuple[str, ...]
    code_terms: Tuple[str, ...]


# 这里维护的是通用业务词汇，而不是问题模板。词条只描述单个概念在代码中可能如何
# 命名，不包含具体项目、类名、方法名、表名或某条业务规则。新增领域应优先补充
# 可复用名词和动作，禁止把一个完整问句直接映射到一组实现符号。
SEMANTIC_CONCEPTS: Tuple[SemanticConcept, ...] = (
    SemanticConcept("display", ("前端", "展示", "显示", "页面"), ("show", "display")),
    SemanticConcept("diamond", ("钻石",), ("diamond",)),
    SemanticConcept("coin", ("金币",), ("coin",)),
    SemanticConcept("gear", ("档位", "阶梯"), ("gear",)),
    SemanticConcept("balance", ("余额", "钱包"), ("balance", "wallet")),
    SemanticConcept("anchor", ("主播",), ("anchor",)),
    SemanticConcept("user", ("用户",), ("user",)),
    SemanticConcept("region", ("区域", "地区"), ("region", "area")),
    SemanticConcept("config", ("配置",), ("config", "conf")),
    SemanticConcept("status", ("状态",), ("status",)),
    SemanticConcept(
        "limit",
        ("上限", "下限", "限制", "阈值", "门槛", "最大", "最小"),
        ("limit", "threshold", "max", "min"),
    ),
    SemanticConcept("withdrawal", ("提现",), ("withdraw",)),
    SemanticConcept("fee", ("手续费", "费率"), ("fee", "rate")),
    SemanticConcept("amount", ("金额",), ("amount",)),
    SemanticConcept("exchange", ("兑换",), ("exchange", "convert")),
    SemanticConcept("settlement", ("结算",), ("settlement", "settle")),
    SemanticConcept("usd", ("美元",), ("usd", "dollar")),
    SemanticConcept("fuse", ("熔断",), ("fuse",)),
    SemanticConcept("trigger", ("触发",), ("trigger",)),
    SemanticConcept("spaceship", ("飞船",), ("spaceship",)),
    SemanticConcept("fragment", ("碎片",), ("fragment",)),
    SemanticConcept("annual", ("年度",), ("annual", "year")),
    SemanticConcept("gold_owner", ("金主",), ("goldowner",)),
    SemanticConcept("prize", ("奖励", "发奖"), ("prize", "reward")),
    SemanticConcept("allocation", ("分配", "发放"), ("allocate", "allocation", "distribute")),
    SemanticConcept("gift", ("礼物",), ("gift",)),
    SemanticConcept("risk", ("风控",), ("risk",)),
    SemanticConcept("freeze", ("冻结",), ("freeze",)),
    SemanticConcept("daily", ("每日", "每天"), ("daily",)),
    SemanticConcept("invite", ("邀请",), ("invite",)),
)

_ASCII_TERM_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _term_in_text(term: str, folded_text: str) -> bool:
    """判断一个概念词是否出现在给定文本中。

    参数:
        - `term`：中文触发词或英文代码词，类型为 `str`。
        - `folded_text`：已经转为小写的待扫描源码或问题文本，类型为 `str`。

    Returns:
        词条存在时返回 `True`。中文按子串匹配；三字符以内的英文词要求完整单词边界，
        避免 `usd` 误命中普通单词、减少短英文概念带来的噪声。
    """
    folded_term = term.casefold().strip()
    if not folded_term:
        return False
    if _ASCII_TERM_PATTERN.fullmatch(term) and len(folded_term) <= 3:
        return re.search(
            rf"(?<![a-z0-9]){re.escape(folded_term)}(?![a-z0-9])",
            folded_text,
        ) is not None
    return folded_term in folded_text


def detect_semantic_concepts(*texts: str) -> Tuple[SemanticConcept, ...]:
    """从问题或源码片段中识别可组合的业务概念。

    参数:
        - `*texts`：需要扫描的用户问题、命中注释或源码文本；每个值类型为 `str`，
          调用方可以一次传入问题和若干高质量首轮命中片段。

    Returns:
        按 `SEMANTIC_CONCEPTS` 声明顺序返回命中的概念。只返回概念定义，不返回
        任何具体项目符号；调用方仍需通过代码检索和源码行号确认事实。
    """
    haystack = "\n".join(str(text or "") for text in texts)
    if not haystack.strip():
        return ()
    folded = haystack.casefold()
    matches: List[SemanticConcept] = []
    for concept in SEMANTIC_CONCEPTS:
        terms = (*concept.triggers, *concept.code_terms)
        if any(_term_in_text(term, folded) for term in terms):
            matches.append(concept)
    return tuple(matches)


def build_semantic_query_plan(
    question: str,
    *,
    seed_hits: Sequence[Mapping[str, Any]] = (),
    max_code_terms: int = 24,
) -> Dict[str, Any]:
    """根据问题和高可信首轮命中生成下一轮跨语言检索计划。

    参数:
        - `question`：用户原始诊断问题，类型为 `str`。
        - `seed_hits`：首轮中文锚点命中的源码行，按相关度从高到低排列，类型为
          `Sequence[Mapping[str, Any]]`。只读取内容，不把命中当作事实。
        - `max_code_terms`：本轮最多生成的英文概念词数，类型为 `int`。限制数量是
          为了控制大型仓库中的候选规模，避免通用词无限扩张。

    Returns:
        包含 `concepts`、`concept_match_terms` 和 `code_terms` 的字典。
        `concepts` 保存概念名到英文检索词的映射，`concept_match_terms` 同时包含
        中文触发词，用于判断某个代码行实际覆盖了多少个业务概念。
    """
    if max_code_terms < 1 or max_code_terms > 60:
        raise ValueError("语义扩展词数量必须在 1 到 60 之间")
    seed_texts: List[str] = []
    for hit in seed_hits[:8]:
        content = str(hit.get("content") or "").strip()
        if content:
            seed_texts.append(content)
    concepts = detect_semantic_concepts(question, *seed_texts)
    concept_code_terms: Dict[str, List[str]] = {}
    concept_match_terms: Dict[str, List[str]] = {}
    code_terms: List[str] = []
    for concept in concepts:
        selected_code_terms: List[str] = []
        for term in concept.code_terms:
            if term.casefold() in {item.casefold() for item in code_terms}:
                continue
            if len(code_terms) >= max_code_terms:
                break
            code_terms.append(term)
            selected_code_terms.append(term)
        if not selected_code_terms:
            continue
        concept_code_terms[concept.name] = selected_code_terms
        concept_match_terms[concept.name] = list(
            dict.fromkeys([*concept.triggers, *concept.code_terms])
        )
    return {
        "concepts": concept_code_terms,
        "concept_match_terms": concept_match_terms,
        "code_terms": code_terms,
    }


def annotate_hits_with_concepts(
    hits: Iterable[Mapping[str, Any]],
    concept_match_terms: Mapping[str, Iterable[str]],
) -> List[Dict[str, Any]]:
    """给代码命中补充概念覆盖信息，供证据排序和发现范围收敛。

    参数:
        - `hits`：通用检索返回的源码行，类型为 `Iterable[Mapping[str, Any]]`。
        - `concept_match_terms`：概念名到中文/英文匹配词的映射，类型为
          `Mapping[str, Iterable[str]]`。

    Returns:
        新的命中列表，每项增加 `matched_concepts`。概念覆盖只说明该行同时出现
        哪些业务词，不代表这些词之间已经形成业务因果关系。
    """
    prepared = {
        str(name): tuple(str(term) for term in terms if str(term).strip())
        for name, terms in concept_match_terms.items()
    }
    annotated: List[Dict[str, Any]] = []
    for hit in hits:
        item = dict(hit)
        content = str(item.get("content") or "").casefold()
        item["matched_concepts"] = [
            name
            for name, terms in prepared.items()
            if any(_term_in_text(term, content) for term in terms)
        ]
        annotated.append(item)
    return annotated


def infer_preferred_repository(hits: Sequence[Mapping[str, Any]]) -> str:
    """根据首轮最高相关命中推断本轮应优先检索的项目子仓库。

    参数:
        - `hits`：已经排序的首轮命中，类型为 `Sequence[Mapping[str, Any]]`。每项
          应包含项目内相对文件路径。

    Returns:
        出现次数最多的第一级目录名，例如 `waky4_miyouphp` 或
        `waky6_miyouactivity`；无法判断时返回空字符串。该值只用于提高同一子仓
        权重，不会排除其他目录中的真实调用链。
    """
    scores: Dict[str, int] = {}
    for rank, hit in enumerate(hits[:12]):
        file_name = str(hit.get("file") or "").replace("\\", "/").strip("/")
        if not file_name or "/" not in file_name:
            continue
        repository = file_name.split("/", 1)[0]
        matched_terms = list(hit.get("matched_queries") or [])
        scores[repository] = scores.get(repository, 0) + max(1, len(matched_terms)) * (12 - rank)
    if not scores:
        return ""
    return max(scores, key=lambda value: (scores[value], value))


__all__ = [
    "SEMANTIC_CONCEPTS",
    "SemanticConcept",
    "annotate_hits_with_concepts",
    "build_semantic_query_plan",
    "detect_semantic_concepts",
    "infer_preferred_repository",
]
