"""未知业务问题的通用检索规划。

该模块不依赖人工领域映射：它把中文问题命中到的注释、配置和文档位置，与项目
PHP 符号索引关联，自动生成下一轮类名、方法名、配置键和文件名词。生成结果只是
检索候选，必须继续通过源码搜索和证据过滤后才能进入诊断结论。
"""

import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# 代码语法和类型名不是业务检索目标。过滤它们可以避免通用回退把每个 PHP 文件
# 共有的 return/function/string 等词变成噪声搜索。
_RESERVED_IDENTIFIERS = {
    "abstract",
    "array",
    "bool",
    "boolean",
    "break",
    "callable",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "declare",
    "default",
    "echo",
    "else",
    "elseif",
    "empty",
    "enddeclare",
    "endfor",
    "endforeach",
    "endif",
    "endswitch",
    "endwhile",
    "extends",
    "false",
    "final",
    "finally",
    "float",
    "for",
    "foreach",
    "function",
    "global",
    "if",
    "implements",
    "include",
    "instanceof",
    "int",
    "interface",
    "isset",
    "iterable",
    "mixed",
    "namespace",
    "new",
    "null",
    "object",
    "parent",
    "private",
    "protected",
    "public",
    "require",
    "return",
    "self",
    "static",
    "string",
    "switch",
    "throw",
    "trait",
    "true",
    "try",
    "unset",
    "use",
    "void",
    "while",
    "yield",
    "accept",
    "admin",
    "api",
    "app",
    "data",
    "description",
    "false",
    "input",
    "json",
    "name",
    "output",
    "request",
    "response",
    "result",
    "stored",
    "title",
    "true",
    "type",
    "usdt",
    "user",
    "value",
    "waky6",
    "waky7",
}

_COMMON_FILE_STEMS = {
    "common",
    "config",
    "controller",
    "index",
    "model",
    "service",
    "services",
    "test",
    "tests",
}

# 这些概念只描述通用的软件对象或结构，单独命中时不足以证明检索已经落到
# 用户真正询问的业务上。支撑门禁要求至少一个“领域概念”参与共现，避免
# “用户 + 阈值”之类泛词组合被误当成风控冻结规则。
_GENERIC_SUPPORT_CONCEPTS = {
    "amount",
    "config",
    "count",
    "daily",
    "display",
    "gift",
    "limit",
    "status",
    "user",
}

# 离线哈希向量只用于测试和兼容，不能绕过源码业务锚点门禁。这里按模型标识
# 再设一道防御，避免调用方误把 `semantic_vector=True` 传给旧索引。
_OFFLINE_EMBEDDING_MODELS = {"hash-v1"}

_CLASS_REFERENCE_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z0-9_]{2,}(?:::[A-Za-z_][A-Za-z0-9_]*)?\b"
)
_QUALIFIED_CALL_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9_]{1,})::([A-Za-z_][A-Za-z0-9_]{2,})\s*\("
)
_FUNCTION_REFERENCE_PATTERN = re.compile(r"\b([a-z_][A-Za-z0-9_]{3,})\s*\(")
_QUOTED_KEY_PATTERN = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_.:-]{3,})['\"]")


def _file_priority(file_name: str) -> int:
    """计算通用命中文件的源码可信度权重。

    参数:
        - `file_name`：命中的项目内相对路径，类型为 `str`。

    Returns:
        整数权重。生产 PHP、配置和业务文档优先；测试、语言包、vendor 和生成
        目录降低权重，避免测试桩或翻译文案成为未知业务的主要召回入口。
    """
    lowered = f"/{file_name.lower()}"
    score = 0
    if "miyouactivity/crontab/" in lowered:
        score += 30
    elif "miyouactivity/action_activity/" in lowered:
        score += 26
    elif "miyouactivity/logic/" in lowered or "miyouactivity/config/" in lowered:
        score += 24
    elif "/php/domain/" in lowered or "/php/services/" in lowered:
        score += 18
    elif "/php/action/" in lowered or "/php/model/" in lowered:
        score += 15
    elif "/php/conf/" in lowered or "/php/config/" in lowered:
        score += 14
    elif "/php/" in lowered:
        score += 9
    if lowered.endswith(".sql"):
        score += 8
    if "/docs/" in lowered or lowered.endswith(".md"):
        score += 4
    if "/reports/" in lowered:
        score -= 6
    if "/miyouadmin/" in lowered or "/admin/" in lowered:
        score -= 5
    if "/phpunit/" in lowered or "/tests/" in lowered:
        score -= 8
    if "/lang/" in lowered:
        score -= 4
    if "/vendor/" in lowered or "/node_modules/" in lowered:
        score -= 20
    return score


def _maximal_matched_terms(values: Iterable[object]) -> List[str]:
    """去除同一句话中互为子串的嵌套检索词。

    参数:
        - `values`：命中行记录的通用检索片段，类型为 `Iterable[object]`；调用方
          可能同时提供“商品碎片”“商品”“碎片”等重叠词。

    Returns:
        只保留不被更完整检索词包含的最长片段。这样“商品碎片”的命中不会被
        嵌套二元词重复计分，也不会让一个泛化“活动”词仅靠多次出现压过真实业务词。
    """
    terms = sorted(
        {
            str(value).strip()
            for value in values
            if str(value).strip()
        },
        key=len,
        reverse=True,
    )
    return [
        term
        for term in terms
        if not any(term != other and term in other for other in terms)
    ]


def rank_discovery_hits(
    hits: Iterable[Dict[str, Any]],
    limit: int = 60,
    *,
    preferred_repository: str = "",
) -> List[Dict[str, Any]]:
    """按业务概念覆盖、命中稀有度和源码可信度排序通用召回行。

    参数:
        - `hits`：`search_code_terms` 返回的代码或文档命中，类型为
          `Iterable[Dict[str, Any]]`。
        - `limit`：返回给索引关联阶段的最大命中行数，类型为 `int`。
        - `preferred_repository`：首轮高可信命中所在的子仓库，例如
          `waky4_miyouphp`，类型为 `str`。只提高同子仓权重，不排除其他目录。

    Returns:
        稳定排序的命中列表。同时覆盖更多业务概念、命中稀有业务片段且与首轮
        来源处于同一子仓的行优先；测试、语言包和依赖目录靠后。不会仅凭命中
        数量断言业务含义。
    """
    if limit < 1 or limit > 500:
        raise ValueError("通用命中排序数量必须在 1 到 500 之间")
    hit_list = list(hits)
    frequencies: Dict[str, int] = {}
    for item in hit_list:
        for normalized in _maximal_matched_terms(item.get("matched_queries") or []):
            frequencies[normalized] = frequencies.get(normalized, 0) + 1
    total_hits = max(1, len(hit_list))
    directory_specific_terms: Dict[str, set] = {}
    for item in hit_list:
        file_name = str(item.get("file") or "")
        directory = str(Path(file_name).parent)
        for value in _maximal_matched_terms(item.get("matched_queries") or []):
            if len(value) >= 4:
                directory_specific_terms.setdefault(directory, set()).add(value)
    scored = []
    for index, item in enumerate(hit_list):
        file_name = str(item.get("file") or "")
        matched = _maximal_matched_terms(item.get("matched_queries") or [])
        longest = max((len(value) for value in matched), default=0)
        coverage = sum(
            min(len(value), 12)
            * (1.0 + math.log((total_hits + 1) / (frequencies.get(value, 1) + 1)))
            for value in matched
        )
        # 一个目录中出现的四字业务片段越多，越像完整业务模块。例如目标目录
        # 同时出现“商品碎片”和“年度金主”，而旧年目录通常只有通用活动标题；
        # 目录级协同权重让服务、奖品和随机发放类一起进入候选，不要求每个方法
        # 注释都重复写完整业务名词。
        specific_terms = directory_specific_terms.get(str(Path(file_name).parent), set())
        directory_bonus = min(180, len(specific_terms) * 45)
        matched_concepts = [str(value) for value in item.get("matched_concepts") or []]
        concept_bonus = len(matched_concepts) * 70
        if len(matched_concepts) >= 2:
            concept_bonus += 60
        anchor_bonus = 0
        if item.get("retrieval_stage") == "business_anchor" and longest >= 4:
            # 中文业务锚点来自用户问题原句，信息量高于跨语言概念共现。
            # 否则“碎片 + 奖品”这类两个通用英文概念会压过真正的“商品碎片”
            # 配置块，使报告只看到方法调用而看不到逐级数值。
            anchor_bonus = 240
        explicit_bonus = 260 if item.get("retrieval_stage") == "explicit_symbol" else 0
        semantic_vector_bonus = 0
        if (
            item.get("retrieval_stage") == "semantic_vector"
            and item.get("semantic_vector") is True
            and str(item.get("embedding_model") or "") not in _OFFLINE_EMBEDDING_MODELS
        ):
            # 真实语义候选可能没有关键字面重合，因此不能用中文长度和词频评分。
            # 分数把强相似候选推进符号发现窗口；弱候选仍低于显式符号和直接锚点。
            try:
                vector_score = max(0.0, min(1.0, float(item.get("vector_score") or 0.0)))
            except (TypeError, ValueError):
                vector_score = 0.0
            semantic_vector_bonus = 230 + int(vector_score * 220)
            if item.get("semantic_vector_strong") is True:
                semantic_vector_bonus += 80
        repository_bonus = 0
        normalized_file = file_name.replace("\\", "/").strip("/")
        if preferred_repository and "/" in normalized_file:
            if normalized_file.split("/", 1)[0] == preferred_repository:
                repository_bonus = 90
        score = int(
            longest * 4
            + coverage * 2
            + len(matched) * 3
            + _file_priority(file_name)
            + directory_bonus
            + concept_bonus
            + anchor_bonus
            + explicit_bonus
            + semantic_vector_bonus
            + repository_bonus
        )
        scored.append((score, index, item))
    scored.sort(key=lambda entry: (-entry[0], str(entry[2].get("file") or ""), int(entry[2].get("line") or 0), entry[1]))
    return [item for _, _, item in scored[:limit]]


def select_semantic_hits(
    hits: Iterable[Dict[str, Any]],
    limit: int = 36,
) -> List[Dict[str, Any]]:
    """选择用于符号发现的高覆盖命中，并保留业务锚点与概念代表行。

    参数:
        - `hits`：已按语义相关度排序的通用命中，类型为
          `Iterable[Dict[str, Any]]`；每项可包含 `matched_concepts`。
        - `limit`：最多返回的高聚焦命中数，类型为 `int`。

    Returns:
        优先保留少量高可信中文业务锚点，再选择覆盖至少两个业务概念的行，并为
        每个概念补充高排名代表行。这样既不会让“min”“rate”等通用英文词形成的
        高覆盖行挤掉问题原句直接命中的业务注释，也不会因为严格阈值丢掉只使用
        单一术语的关键配置。
    """
    if limit < 1 or limit > 200:
        raise ValueError("语义聚焦命中数量必须在 1 到 200 之间")
    ranked = list(hits)
    concept_counts = [len(item.get("matched_concepts") or []) for item in ranked]
    best_coverage = max(concept_counts, default=0)
    selected: List[Dict[str, Any]] = []
    seen = set()
    semantic_budget = min(12, max(4, limit // 3))
    semantic_added = 0

    def append(item: Dict[str, Any]) -> None:
        """按文件和行号去重追加一条语义命中。

        参数:
            - `item`：候选源码命中，类型为 `Dict[str, Any]`。
        """
        nonlocal semantic_added
        key = (str(item.get("file") or ""), int(item.get("line") or 0))
        is_semantic_vector = item.get("retrieval_stage") == "semantic_vector"
        if is_semantic_vector and semantic_added >= semantic_budget:
            return
        if key in seen or len(selected) >= limit:
            return
        seen.add(key)
        selected.append(item)
        if is_semantic_vector:
            semantic_added += 1

    # 用户显式给出的类或方法命中优先级最高。显式符号可能只出现在类声明行，
    # 没有中文业务注释，也不一定覆盖多个概念；若先按概念筛选，它会在进入索引
    # 关联前被挤掉。这里仍只保留源码文件，文档和语言包中的同名文本不能冒充定义。
    for item in ranked:
        if item.get("retrieval_stage") != "explicit_symbol":
            continue
        lowered = f"/{str(item.get('file') or '').casefold()}"
        if "/docs/" in lowered or lowered.endswith(".md") or "/lang/" in lowered:
            continue
        append(item)
        if len(selected) >= min(6, limit):
            break

    # 中文锚点是问题原句与源码之间最强的可审计联系。锚点先按业务词稀有度排序：
    # “商品碎片”只出现在目标活动配置中的次数，远少于“年度金主”这种活动标题，
    # 因此应优先保留四个等级配置。每个业务词可保留最多六个、行距超过二十行的
    # 代表命中，用来覆盖声明方法、多个同类配置块或多个业务分支；文件、文档和
    # 语言包的过滤规则仍保持不变。
    anchor_candidates = []
    anchor_term_frequency: Dict[str, int] = {}
    anchor_term_order: List[str] = []
    for item in ranked:
        if item.get("retrieval_stage") != "business_anchor":
            continue
        file_name = str(item.get("file") or "")
        lowered = f"/{file_name.casefold()}"
        if "/docs/" in lowered or lowered.endswith(".md") or "/lang/" in lowered:
            continue
        terms = [
            term
            for term in _maximal_matched_terms(item.get("matched_queries") or [])
            if len(term) >= 4
        ]
        if not terms:
            continue
        anchor_candidates.append((item, terms))
        for term in terms:
            anchor_term_frequency[term] = anchor_term_frequency.get(term, 0) + 1
            if term not in anchor_term_order:
                anchor_term_order.append(term)

    anchor_budget = min(18, max(6, limit // 2))
    anchor_term_rank = {
        term: index for index, term in enumerate(anchor_term_order)
    }
    anchor_term_order.sort(
        key=lambda term: (anchor_term_frequency.get(term, 0), anchor_term_rank[term])
    )
    for term in anchor_term_order:
        term_lines = []
        for item, terms in anchor_candidates:
            if term not in terms:
                continue
            line_number = int(item.get("line") or 0)
            if any(abs(line_number - previous) <= 20 for previous in term_lines):
                continue
            append(item)
            term_lines.append(line_number)
            if len(term_lines) >= 6 or len(selected) >= anchor_budget:
                break
        if len(selected) >= anchor_budget:
            break

    # 真实语义向量候选没有中文锚点，不能等待后续概念阈值筛选。锚点之后保留
    # 强候选，再用剩余预算补充分数达标的弱候选，最多十二只用于发现符号和配置；
    # 是否有足够支撑仍由门禁、源码读取和确定性分析决定。
    semantic_candidates = [
        item
        for item in ranked
        if item.get("retrieval_stage") == "semantic_vector"
        and item.get("semantic_vector") is True
        and str(item.get("embedding_model") or "") not in _OFFLINE_EMBEDDING_MODELS
    ]
    semantic_candidates.sort(
        key=lambda item: (
            0 if item.get("semantic_vector_strong") is True else 1,
            -float(item.get("vector_score") or 0.0),
        )
    )
    for item in semantic_candidates:
        append(item)
        if semantic_added >= semantic_budget or len(selected) >= limit:
            break

    threshold = 2 if best_coverage >= 2 else max(1, best_coverage)
    for item in ranked:
        if len(item.get("matched_concepts") or []) >= threshold:
            append(item)
        if len(selected) >= limit:
            return selected

    concept_names = []
    for item in ranked:
        for name in item.get("matched_concepts") or []:
            if str(name) not in concept_names:
                concept_names.append(str(name))
    for concept_name in concept_names:
        for item in ranked:
            if concept_name in (item.get("matched_concepts") or []):
                append(item)
                break
        if len(selected) >= limit:
            break

    for item in ranked:
        append(item)
        if len(selected) >= limit:
            break
    return selected


def discover_related_queries(
    project_index: Dict[str, List[Dict[str, Any]]],
    discovery_hits: Iterable[Dict[str, Any]],
    *,
    existing_queries: Optional[Iterable[str]] = None,
    limit: int = 12,
    preferred_repository: str = "",
) -> Dict[str, Any]:
    """从通用命中文件反向生成类、方法、配置键和业务文件检索词。

    参数:
        - `project_index`：PHP 符号索引，必须包含 `symbols` 和 `configs` 列表，
          类型为 `Dict[str, List[Dict[str, Any]]]`。
        - `discovery_hits`：中文通用片段命中的源码或文档行，类型为
          `Iterable[Dict[str, Any]]`。
        - `existing_queries`：第一轮领域词和显式符号，用于去重；
          类型为 `Optional[Iterable[str]]`。
        - `limit`：最多新增的检索词数量，类型为 `int`。
        - `preferred_repository`：首轮命中确定的优先子仓库，类型为 `str`。

    Returns:
        包含 `queries` 和 `sources` 的字典。候选来源包括命中行中的英文标识符、
        同文件最近的方法/类/配置键以及业务文件名。按与命中行距离和文件可信度
        排序，调用方仍需执行精确搜索验证。
    """
    if limit < 1 or limit > 30:
        raise ValueError("通用扩展检索词数量必须在 1 到 30 之间")
    existing = {
        str(value).strip().casefold()
        for value in existing_queries or []
        if str(value).strip()
    }
    candidates: Dict[str, Dict[str, Any]] = {}
    insertion = 0

    def add_candidate(
        value: str,
        score: int,
        source: Dict[str, Any],
        *,
        trusted: bool = False,
    ) -> None:
        """去重并保留一个通用扩展检索词及其来源说明。

        参数:
            - `value`：准备加入下一轮检索的类名、方法名、配置键或文件名，类型为 `str`。
            - `score`：该候选与当前命中的相关性分数，类型为 `int`。
            - `source`：候选来源的文件、行号和原因，类型为 `Dict[str, Any]`。
            - `trusted`：候选是否已由 PHP 符号索引或配置索引确认，类型为
              `bool`。该标志用于区分可追溯来源，不绕过保留字过滤；大型项目
              也可能存在名为 `User` 的真实类，但它不能代表当前业务问题。
        """
        nonlocal insertion
        normalized = value.strip()
        folded = normalized.casefold()
        if not normalized or folded in existing or len(normalized) < 3:
            return
        if folded in _RESERVED_IDENTIFIERS or normalized.isdigit():
            return
        if normalized.endswith((".", ":", "::", "-", "_")) or ".." in normalized:
            return
        if trusted:
            score += 5
        current = candidates.get(folded)
        if current is None:
            insertion += 1
            candidates[folded] = {
                "query": normalized,
                "score": score,
                "insertion": insertion,
                "source": source,
            }
        elif score > current["score"]:
            current.update({"query": normalized, "score": score, "source": source})

    ranked_hits = rank_discovery_hits(
        discovery_hits,
        limit=40,
        preferred_repository=preferred_repository,
    )
    hit_frequency_by_file: Dict[str, int] = {}
    for hit in ranked_hits:
        hit_file = str(hit.get("file") or "")
        if hit_file:
            hit_frequency_by_file[hit_file] = hit_frequency_by_file.get(hit_file, 0) + 1
    symbols_by_file: Dict[str, List[Dict[str, Any]]] = {}
    configs_by_file: Dict[str, List[Dict[str, Any]]] = {}
    indexed_class_names = set()
    indexed_function_names = set()
    indexed_method_names = set()
    indexed_method_leaf_names = set()
    indexed_config_keys = set()
    for symbol in project_index.get("symbols") or []:
        file_name = str(symbol.get("file") or "")
        symbol_name = str(symbol.get("symbol_name") or "")
        symbol_type = str(symbol.get("symbol_type") or "")
        symbols_by_file.setdefault(file_name, []).append(symbol)
        if symbol_type in {"class", "interface", "trait"}:
            indexed_class_names.add(symbol_name)
        if symbol_type == "function":
            indexed_function_names.add(symbol_name)
        if symbol_type == "method" and "::" in symbol_name:
            indexed_method_names.add(symbol_name)
            indexed_method_leaf_names.add(symbol_name.rsplit("::", 1)[-1])
    for config in project_index.get("configs") or []:
        file_name = str(config.get("file") or "")
        configs_by_file.setdefault(file_name, []).append(config)
        key = str(config.get("key") or "")
        if key and not key.endswith((".", ":", "::", "-", "_")) and ".." not in key:
            indexed_config_keys.add(key)
    for grouped in (symbols_by_file, configs_by_file):
        for items in grouped.values():
            items.sort(key=lambda item: int(item.get("line") or 0))

    # 中文注释通常只出现在某个类或配置文件的一行，单行命中无法代表整个业务
    # 目录。若一个目录已经命中四字业务短语，就把该目录中索引到的类声明一起
    # 加入候选，让实现不同职责的服务、奖品和随机逻辑都能进入第二轮检索。
    business_directories = set()
    for hit in ranked_hits:
        if max(
            (len(value) for value in _maximal_matched_terms(hit.get("matched_queries") or [])),
            default=0,
        ) < 4:
            continue
        business_directories.add(str(Path(str(hit.get("file") or "")).parent))
    for file_name, file_symbols in symbols_by_file.items():
        directory = str(Path(file_name).parent)
        if directory not in business_directories:
            continue
        for symbol in file_symbols:
            if str(symbol.get("symbol_type") or "") not in {"class", "interface", "trait"}:
                continue
            add_candidate(
                str(symbol.get("symbol_name") or ""),
                340 + max(0, _file_priority(file_name)),
                {
                    "stage": "directory_related",
                    "file": file_name,
                    "line": int(symbol.get("line") or 0),
                    "reason": "同一业务目录已命中完整业务短语",
                },
                trusted=True,
            )

    for hit in ranked_hits:
        file_name = str(hit.get("file") or "")
        line_number = int(hit.get("line") or 0)
        content = str(hit.get("content") or "")
        # 同一文件被多个长业务片段重复命中，比孤立的单行命中更能说明该文件
        # 承载当前问题；提升幅度封顶，避免超大后台文件仅凭行数淹没业务服务。
        repeated_business_hits = max(0, hit_frequency_by_file.get(file_name, 1) - 1)
        normalized_file = f"/{file_name.casefold()}"
        is_business_source = "/php/" in normalized_file or "miyouactivity/" in normalized_file
        if not is_business_source or "/phpunit/" in normalized_file or "/tests/" in normalized_file:
            repeated_business_hits = 0
        # 两字泛词在大型项目中会散落在无数文件里；同文件重复命中只有在最长
        # 业务片段达到四字时才视为“该文件集中承载当前规则”。
        maximal_hit_terms = _maximal_matched_terms(hit.get("matched_queries") or [])
        if max((len(value) for value in maximal_hit_terms), default=0) < 4:
            repeated_business_hits = 0
        # 生产活动仓库不一定遵循 `php/services` 目录约定。`miyouactivity` 中的
        # crontab、action_activity 和 Logic 同样承载真实业务规则，因此把文件
        # 可信度持续计入候选分数，避免被普通 PHP 基础设施文件和旧年报告挤掉。
        source_boost = min(48, repeated_business_hits * 12) + max(0, _file_priority(file_name))
        base_source = {
            "stage": "generic_hit",
            "file": file_name,
            "line": line_number,
            "reason": "命中问题自动生成的中文业务片段",
        }
        for identifier in _CLASS_REFERENCE_PATTERN.findall(content):
            if "::" in identifier:
                class_name, method_name = identifier.split("::", 1)
                if class_name in indexed_class_names:
                    add_candidate(class_name, 270 + source_boost, base_source, trusted=True)
                if identifier in indexed_method_names:
                    add_candidate(identifier, 255 + source_boost, base_source, trusted=True)
                if method_name in indexed_method_leaf_names:
                    add_candidate(method_name, 225 + source_boost, base_source, trusted=True)
            elif identifier in indexed_class_names:
                add_candidate(identifier, 275 + source_boost, base_source, trusted=True)
        for class_name, method_name in _QUALIFIED_CALL_PATTERN.findall(content):
            qualified_name = f"{class_name}::{method_name}"
            if qualified_name in indexed_method_names:
                add_candidate(qualified_name, 260 + source_boost, base_source, trusted=True)
                add_candidate(method_name, 230 + source_boost, base_source, trusted=True)
        for identifier in _FUNCTION_REFERENCE_PATTERN.findall(content):
            if identifier in indexed_function_names:
                add_candidate(identifier, 235 + source_boost, base_source, trusted=True)
        for key in _QUOTED_KEY_PATTERN.findall(content):
            if key in indexed_config_keys:
                add_candidate(
                    key,
                    250 + source_boost,
                    {**base_source, "reason": "命中行包含已索引配置键"},
                    trusted=True,
                )

        stem = Path(file_name).stem
        if (
            len(stem) >= 6
            and stem.casefold() not in _COMMON_FILE_STEMS
            and stem in indexed_class_names
        ):
            add_candidate(stem, 260 + source_boost + _file_priority(file_name), {
                **base_source,
                "reason": "命中文件声明了同名索引类",
            }, trusted=True)

        nearby_items = []
        for item in symbols_by_file.get(file_name, []):
            distance = abs(int(item.get("line") or 0) - line_number)
            nearby_items.append((distance, 0, item))
        for item in configs_by_file.get(file_name, []):
            distance = abs(int(item.get("line") or 0) - line_number)
            nearby_items.append((distance, 1, item))
        nearby_items.sort(key=lambda entry: (entry[0], entry[1], str(entry[2].get("symbol_name") or entry[2].get("key") or "")))
        for distance, kind, item in nearby_items[:8]:
            if distance > 160:
                continue
            proximity = max(0, 100 - distance // 2)
            source = {
                "stage": "index_related",
                "file": file_name,
                "line": int(item.get("line") or 0),
                "reason": "命中文件内最近的符号或配置",
            }
            if kind == 0:
                symbol_name = str(item.get("symbol_name") or "")
                symbol_type = str(item.get("symbol_type") or "")
                if symbol_type in {"class", "interface", "trait"}:
                    add_candidate(symbol_name, 285 + source_boost + proximity, source, trusted=True)
                else:
                    add_candidate(symbol_name, 240 + source_boost + proximity, source, trusted=True)
                method_name = symbol_name.rsplit("::", 1)[-1]
                if "::" in symbol_name:
                    add_candidate(method_name, 220 + source_boost + proximity, source, trusted=True)
            else:
                add_candidate(
                    str(item.get("key") or ""),
                    250 + source_boost + proximity,
                    source,
                    trusted=True,
                )

    ranked_candidates = sorted(
        candidates.values(),
        key=lambda item: (-int(item["score"]), int(item["insertion"])),
    )
    # 单个文件只保留六个最强候选。否则大型配置、后台页面或控制器会占满
    # 扩展额度，导致另一个真正实现业务规则的服务文件无法进入第二轮检索。
    ordered = []
    per_file = {}
    for candidate in ranked_candidates:
        file_name = str((candidate.get("source") or {}).get("file") or "")
        if file_name and per_file.get(file_name, 0) >= 6:
            continue
        ordered.append(candidate)
        if file_name:
            per_file[file_name] = per_file.get(file_name, 0) + 1
        if len(ordered) >= limit:
            break
    return {
        "queries": [str(item["query"]) for item in ordered],
        "sources": [
            {
                "query": str(item["query"]),
                "score": int(item["score"]),
                **(item.get("source") or {}),
            }
            for item in ordered
        ],
    }


def assess_retrieval_support(
    hits: Iterable[Dict[str, Any]],
    question_concepts: Iterable[str],
    *,
    explicit_symbols: Iterable[str] = (),
) -> Dict[str, Any]:
    """判断本轮命中是否足以继续扩展符号和调用链。

    参数:
        - `hits`：已完成概念标注的首轮与扩展命中，类型为
          `Iterable[Dict[str, Any]]`。函数只读取检索元数据，不把命中内容当成
          业务事实。
        - `question_concepts`：问题中识别出的可组合概念名，类型为
          `Iterable[str]`。用于检查同一源码行是否真正覆盖了问题需要的多个
          语义，而不是只在全仓库分散出现。
        - `explicit_symbols`：用户问题中显式给出的类、方法或配置标识，类型为
          `Iterable[str]`。显式符号可以绕过中文业务锚点要求，但仍必须经过
          后续源码读取验证。

    Returns:
        包含 `grounded`、`level`、`reason`、`direct_anchor_count`、
        `best_concept_coverage` 和 `required_concept_coverage` 的字典。
        `grounded=False` 表示应停止调用链扩展并明确报告业务锚点缺失，避免
        用泛词扩散出大量看似相关、实际不能回答问题的证据。
    """
    concept_list = list(
        dict.fromkeys(str(value) for value in question_concepts if str(value))
    )
    concept_set = set(concept_list)
    domain_concepts = concept_set - _GENERIC_SUPPORT_CONCEPTS
    hit_list = list(hits)
    direct_anchor_count = 0
    rejected_generic_anchor_count = 0
    semantic_vector_count = 0
    strong_semantic_vector_count = 0
    best_vector_score = 0.0
    for item in hit_list:
        vector_file = str(item.get("file") or "").replace("\\", "/").strip("/")
        vector_path = f"/{vector_file.casefold()}"
        vector_extension = f".{vector_file.rsplit('.', 1)[-1].lower()}" if "." in vector_file else ""
        if (
            item.get("retrieval_stage") == "semantic_vector"
            and item.get("semantic_vector") is True
            and item.get("vector_compatible") is True
            and item.get("embedding_provider") == "openai_compatible"
            and str(item.get("embedding_model") or "") not in _OFFLINE_EMBEDDING_MODELS
            and vector_extension in {".php", ".inc", ".phtml"}
            and not any(
                marker in vector_path
                for marker in ("/tests/", "/phpunit/", "/vendor/", "/node_modules/", "/docs/", "/lang/")
            )
        ):
            semantic_vector_count += 1
            try:
                vector_score = max(0.0, min(1.0, float(item.get("vector_score") or 0.0)))
            except (TypeError, ValueError):
                vector_score = 0.0
            best_vector_score = max(best_vector_score, vector_score)
            if item.get("semantic_vector_strong") is True:
                strong_semantic_vector_count += 1
        if item.get("retrieval_stage") != "business_anchor":
            continue
        file_name = str(item.get("file") or "").replace("\\", "/").casefold()
        if (
            "/docs/" in f"/{file_name}"
            or file_name.endswith(".md")
            or "/lang/" in f"/{file_name}"
        ):
            continue
        longest_term = max(
            (len(str(value)) for value in item.get("matched_queries") or []),
            default=0,
        )
        if longest_term >= 4:
            item_concepts = {
                str(value) for value in item.get("matched_concepts") or []
            }
            item_domain_concepts = item_concepts & domain_concepts
            # 当业务概念目录能够解释该锚点时，“每日兑换”这类只含一个领域动作、
            # 其余均为通用概念的组合不能单独证明问题涉及的未知业务对象存在。
            # 若源码使用完全不在目录中的新业务词，item_concepts 为空，仍保留
            # 原来的四字中文锚点路径，避免词表变成业务准入白名单。
            if item_concepts and len(item_domain_concepts) < 2:
                rejected_generic_anchor_count += 1
                continue
            direct_anchor_count += 1

    best_concept_coverage = 0
    best_domain_coverage = 0
    for item in hit_list:
        covered = concept_set.intersection(
            str(value) for value in item.get("matched_concepts") or []
        )
        best_concept_coverage = max(best_concept_coverage, len(covered))
        best_domain_coverage = max(best_domain_coverage, len(covered & domain_concepts))

    # 两个概念的问题要求两个概念同行；更复杂的问题最多要求三个概念同行。
    # 领域概念还必须覆盖至少两个，从而排除“设备 + 用户 + 阈值”这类只命中
    # 一个业务对象、却遗漏“注册”动作的伪锚点。只有单个领域概念的问题仍按
    # 一个领域概念判定，避免把未知领域的新名词拒之门外。显式符号由用户直接
    # 指定，不受中文锚点限制。
    required_coverage = min(3, max(1, len(concept_set)))
    required_domain_coverage = min(2, len(domain_concepts))
    semantic_grounded = (
        best_concept_coverage >= required_coverage
        and (
            not domain_concepts
            or best_domain_coverage >= required_domain_coverage
        )
    )
    if explicit_symbols:
        level = "explicit_symbol"
        reason = "问题包含显式代码符号，允许继续读取对应定义和直接调用链"
        grounded = True
    elif direct_anchor_count:
        level = "business_anchor"
        reason = "源码中存在问题原句的四字以上中文业务锚点"
        grounded = True
    elif strong_semantic_vector_count:
        level = "semantic_vector"
        reason = "真实 embedding 找到强相似源码候选，允许继续做符号发现、源码读取和调用链验证"
        grounded = True
    elif semantic_grounded:
        level = "semantic_concepts"
        reason = "同一源码命中覆盖了足够的业务概念和至少一个领域概念"
        grounded = True
    else:
        level = "insufficient"
        reason = "源码只命中泛化词或未共现问题所需业务概念，应停止扩展并报告缺口"
        grounded = False
    return {
        "grounded": grounded,
        "level": level,
        "reason": reason,
        "direct_anchor_count": direct_anchor_count,
        "best_concept_coverage": best_concept_coverage,
        "best_domain_coverage": best_domain_coverage,
        "semantic_vector_count": semantic_vector_count,
        "strong_semantic_vector_count": strong_semantic_vector_count,
        "best_vector_score": round(best_vector_score, 6),
        "required_concept_coverage": required_coverage,
        "required_domain_coverage": required_domain_coverage,
        "rejected_generic_anchor_count": rejected_generic_anchor_count,
    }


def config_definition_terms(
    config_keys: Iterable[str],
    *,
    limit: int = 8,
) -> List[str]:
    """从点号配置键提取可用于定位定义处的末级字段名。

    参数:
        - `config_keys`：索引中发现的配置读取键，例如
          `anchor.diamond_to_usd`，类型为 `Iterable[str]`。完整键通常只能命中
          `Config::get()` 调用点，末级字段名才能继续命中配置文件里的
          `'diamond_to_usd' => ...` 定义行。
        - `limit`：最多返回的末级字段名数量，类型为 `int`。限制用于防止大型
          仓库的大量配置读取挤占最终源码检索预算。

    Returns:
        去重、保持发现顺序的合法 PHP 标识符列表。末尾带点、包含动态占位符或
        不是点号配置键的输入会被忽略；返回值只是检索词，配置值仍必须通过源码
        读取和确定性分析验证。
    """
    if limit < 1 or limit > 20:
        raise ValueError("配置定义检索词数量必须在 1 到 20 之间")
    terms: List[str] = []
    for value in config_keys:
        key = str(value or "").strip()
        if "." not in key:
            continue
        leaf = key.rsplit(".", 1)[-1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{2,}", leaf):
            continue
        if leaf not in terms:
            terms.append(leaf)
        if len(terms) >= limit:
            break
    return terms


def _concept_token_matches(question_term: str, key_term: str) -> bool:
    """判断问题概念词与配置键片段是否属于同一英文词形。

    参数:
        - `question_term`：问题理解生成并已规范化为小写字母数字的英文概念，
          例如 `withdraw`、`limit` 或 `diamond`。
        - `key_term`：完整配置键拆分出的单个英文片段，例如 `withdrawal`、
          `limits` 或 `diamonds`。

    返回:
        精确相同，或仅相差常见名词、形容词和动词后缀时返回 `True`。该规则只
        处理 `withdraw/withdrawal`、`limit/limits`、`diamond/diamonds` 这类
        通用词形变化，不把任意较长前缀都视为匹配，避免配置键候选被放大。
    """
    if question_term == key_term:
        return True
    shorter, longer = sorted(
        (question_term, key_term),
        key=len,
    )
    if len(shorter) < 4 or not longer.startswith(shorter):
        return False
    return longer[len(shorter):] in {
        "s",
        "es",
        "ed",
        "ing",
        "al",
        "tion",
        "ation",
    }


def select_question_config_keys(
    project_index: Dict[str, List[Dict[str, Any]]],
    question_terms: Iterable[str],
    *,
    limit: int = 4,
) -> List[str]:
    """从完整项目索引中选择与问题概念组合最吻合的配置读取键。

    参数:
        - `project_index`：当前项目符号索引，必须包含 `configs`；每项至少提供
          `key`、`file` 和 `line`，类型为 `Dict[str, List[Dict[str, Any]]]`。
        - `question_terms`：问题理解生成的英文代码概念，例如设备注册问题中的
          `device`、`registration`、`count`、`limit`，类型为 `Iterable[str]`。
        - `limit`：最多返回的配置键数量，类型为 `int`。限制用于避免大量相邻
          配置读取占满后续源码检索预算。

    Returns:
        按概念覆盖度和键名具体度排序的完整配置键。函数只做索引候选选择，不读取
        配置值；调用方仍须通过源码证据解析 `getByTestMode` 等环境覆盖表达式。
    """
    if limit < 1 or limit > 12:
        raise ValueError("问题配置键数量必须在 1 到 12 之间")
    normalized_terms = {
        re.sub(r"[^a-z0-9]", "", str(value).casefold())
        for value in question_terms
        if str(value).strip()
    }
    normalized_terms = {value for value in normalized_terms if len(value) >= 3}
    if not normalized_terms:
        return []

    scores: Dict[str, Dict[str, Any]] = {}
    for order, item in enumerate(project_index.get("configs") or []):
        key = str(item.get("key") or "").strip()
        if "." not in key:
            continue
        segments = re.split(r"[._:/-]+", key)
        key_terms = set()
        for segment in segments:
            camel_parts = re.findall(
                r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|\d+",
                segment,
            )
            for part in camel_parts or [segment]:
                normalized = re.sub(r"[^a-z0-9]", "", part.casefold())
                if len(normalized) >= 3:
                    key_terms.add(normalized)
        matched = {
            key_term
            for key_term in key_terms
            if any(_concept_token_matches(term, key_term) for term in normalized_terms)
        }
        if len(matched) < 2:
            continue
        current = scores.get(key)
        candidate = {
            "key": key,
            "coverage": len(matched),
            "specificity": sum(len(value) for value in matched),
            "file": str(item.get("file") or ""),
            "line": int(item.get("line") or 0),
            "order": order,
        }
        if current is None or (
            candidate["coverage"],
            candidate["specificity"],
            -candidate["order"],
        ) > (
            current["coverage"],
            current["specificity"],
            -current["order"],
        ):
            scores[key] = candidate
    ranked = sorted(
        scores.values(),
        key=lambda item: (
            -int(item["coverage"]),
            -int(item["specificity"]),
            str(item["file"]),
            int(item["line"]),
        ),
    )
    return [str(item["key"]) for item in ranked[:limit]]


__all__ = [
    "assess_retrieval_support",
    "config_definition_terms",
    "discover_related_queries",
    "rank_discovery_hits",
    "select_question_config_keys",
    "select_semantic_hits",
]
