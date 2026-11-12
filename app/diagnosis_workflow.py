"""诊断执行工作流、检索编排和证据持久化。

该模块不负责 Celery 应用和队列声明，只提供可被任务适配层、测试或未来 API
直接调用的诊断执行能力。所有代码仓库访问仍通过受控只读工具完成。
"""

import asyncio
import logging
import os
import re
import time
from typing import Optional

from billiard.exceptions import SoftTimeLimitExceeded
from app.task_store import (
    clear_task_evidence,
    create_pool,
    get_task,
    insert_evidence,
    record_trace_span,
    update_task,
)
from app.task_store import replace_symbol_index_if_changed
from app.business_analysis import build_deterministic_findings
from app.settings import get_settings
from app.code_tools import (
    CodeToolError,
    extract_generic_anchors,
    extract_search_queries,
    read_file,
    search_code_terms,
)
from app.php_index import trace_index_call_chain
from app.query_understanding import (
    annotate_hits_with_concepts,
    build_semantic_query_plan,
    infer_preferred_repository,
)
from app.retrieval_planner import (
    assess_retrieval_support,
    config_definition_terms,
    discover_related_queries,
    rank_discovery_hits,
    select_question_config_keys,
    select_semantic_hits,
)
from app.repository_history import (
    history_search_terms,
    is_history_question,
    select_history_terms_from_current_hits,
    search_repository_history,
)
from app.project_index_cache import load_project_symbol_index
from app.llm_diagnosis import count_available_evidence, generate_ai_diagnosis_cached
from app.reporting import build_assessment
from app.retrieval.channels import SearchTermsChannel
from app.retrieval.contracts import RetrievalRequest
from app.retrieval.engine import RetrievalEngine
from app.retrieval.semantic_vectors import search_semantic_vector_candidates
from app.symbol_tools import select_call_chain_entries, summarize_symbol_index
from app.validation_runner import execute_validation_plan

settings = get_settings()
logger = logging.getLogger(__name__)

# 一次诊断最多保留的代码命中数。每个命中会扩展到相邻源码行，数量继续增大
# 会同时拖慢持久化、Redis 和 LLM 请求。六十四个证据足以覆盖入口、配置、
# 校验和多层调用链；没有业务锚点时则完全不保留扩展证据。
MAX_DIAGNOSTIC_EVIDENCE = 64

# 通用召回负责发现“下一步该搜索哪个真实符号”，需要在年度活动等大型仓库中
# 为同类业务文件保留足够候选。最终进入证据表的仍会压缩到
# `MAX_DIAGNOSTIC_EVIDENCE`，因此提高召回上限不会同比放大 LLM 上下文。
MAX_GENERIC_HITS = 200

# 精确检索在完成动态符号发现后一次批量执行。五百条只用于候选排序，最终仍会
# 压缩到 `MAX_DIAGNOSTIC_EVIDENCE`；批量上限同时受底层代码工具约束，避免
# 对挂载磁盘进行与检索词数量相同的重复全仓扫描。
MAX_TERM_SEARCH_HITS = 500

# 显式类问题经常把主流程留给父类实现。这里只读取 `extends` 声明的父类名，
# 再回到现有符号索引定位定义文件，不保存任何项目类名或继承关系映射。
_PARENT_CLASS_PATTERN = re.compile(
    r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*\s+extends\s+([A-Za-z_][A-Za-z0-9_\\]*)"
)


def _explicit_symbol_queries(queries: list[str]) -> list[str]:
    """筛选问题中可用于源码验证的显式类、方法或配置标识。

    参数:
        - `queries`：`extract_search_queries` 从用户问题提取的英文标识和中文
          业务词，类型为 `list[str]`。

    Returns:
        保持原顺序的显式符号候选。带 `::`、点号配置键、下划线或明显类名的
        标识会进入首轮精确检索；`API`、`SQL`、`Redis` 等常见技术缩写不单独
        作为业务类名，避免用户问 Redis key 时把整个缓存层当作目标入口。
    """
    common_tokens = {
        "API", "HTTP", "HTTPS", "ID", "JSON", "PHP", "REDIS", "SQL", "URL",
        "UUID", "YAML",
    }
    result = []
    for value in queries:
        query = str(value or "").strip()
        if not query:
            continue
        if "::" in query or "." in query or "_" in query:
            result.append(query)
            continue
        if query.upper() in common_tokens:
            continue
        # 纯大写的技术缩写（API、SQL）不当作业务类名，但带数字的活动标识
        # （FIFA2026）和小写业务标识（fupay）必须保留，否则问题中的确定性
        # 入口会被长度和大小写规则误删，检索只能退回泛化中文锚点。
        if query[0].isupper() and len(query) >= 4 and (
            any(char.isdigit() for char in query) or not query.isupper()
        ):
            result.append(query)
            continue
        if query[0].islower() and len(query) >= 4 and query.isalpha():
            result.append(query)
    return list(dict.fromkeys(result))


def _concept_config_file_evidence(
    root: str,
    project: str,
    terms: list[str],
    *,
    limit: int = 3,
) -> list[dict]:
    """把概念英文词命中的配置文件名词干加入证据。

    参数:
        - `root`：被诊断仓库白名单根目录，类型为 `str`。
        - `project`：当前项目标识，类型为 `str`。
        - `terms`：问题理解生成的概念英文词，例如 `noble`、`newnoblelv`，类型为
          `list[str]`。只使用长度不少于六个字符的词，降低通用词误命中。
        - `limit`：最多补充的配置文件数量，类型为 `int`。限制用于避免把大量
          同名配置塞入证据预算。

    Returns:
        配置文件首行证据列表。只在目录名为 `config`/`conf` 下做词干全等匹配，
        不读取业务数据库，也不根据文件名直接生成结论；值仍由确定性分析器从
        完整配置窗口中解析。
    """
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(term).casefold())
        for term in terms
        if len(str(term).strip()) >= 6
    }
    normalized.discard("")
    if not normalized:
        return []
    base = os.path.join(root, project)
    skipped = {
        ".git",
        ".gitnexus",
        ".idea",
        "logs",
        "node_modules",
        "templates_c",
        "tmp",
        "vendor",
    }
    results: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [name for name in dirnames if name not in skipped]
        if os.path.basename(dirpath).casefold() not in {"config", "conf"}:
            continue
        for name in filenames:
            if not name.endswith(".php"):
                continue
            stem = re.sub(r"[^a-z0-9]", "", name[:-4].casefold())
            if stem not in normalized:
                continue
            file_name = os.path.relpath(
                os.path.join(dirpath, name), base
            ).replace(os.sep, "/")
            results.append(
                {
                    "type": "concept_config",
                    "query": stem,
                    "file": file_name,
                    "line": 1,
                    "content": "<?php",
                }
            )
            if len(results) >= limit:
                return results
    return results


def _tag_initial_retrieval_stage(
    item: dict,
    generic_anchors: list[str],
    explicit_symbols: list[str],
) -> str:
    """根据首轮实际命中的词标记源码候选来源。

    参数:
        - `item`：`search_code_terms` 返回的单条源码命中，包含
          `matched_queries`，类型为 `dict`。
        - `generic_anchors`：问题生成的中文业务片段，类型为 `list[str]`。
        - `explicit_symbols`：问题中提取的类、方法或配置标识，类型为
          `list[str]`。

    Returns:
        `business_anchor`、`explicit_symbol` 或空字符串。中文业务锚点和显式
        符号同时命中时优先标记业务锚点，以保留问题原句与源码注释之间的直接
        联系；标记只描述检索来源，不代表业务结论。
    """
    matched = {str(value) for value in item.get("matched_queries") or []}
    anchor_set = set(generic_anchors)
    if any(len(value) >= 4 and value in anchor_set for value in matched):
        return "business_anchor"
    if matched.intersection(explicit_symbols):
        return "explicit_symbol"
    return ""


def _path_scope_tokens(file_path: str) -> set[str]:
    """提取源码路径中可用于消歧的业务目录词。

    参数:
        - `file_path`：项目内相对路径，类型为 `str`。

    Returns:
        长度至少为四的小写路径片段。`php`、`service`、`logic` 等通用目录不参与
        同名类消歧，避免所有候选获得相同分值。
    """
    ignored = {
        "action", "admin", "boot", "common", "config", "controller", "entity",
        "logic", "miyouactivity", "miyouadmin", "miyouphp", "model", "service",
        "services", "static", "utils",
    }
    return {
        part.casefold()
        for part in re.split(r"[/\\]", file_path)
        if len(part) >= 4 and part.casefold() not in ignored
    }


def _select_explicit_target_files(
    project_index: dict,
    evidence: list[dict],
    explicit_symbols: list[str],
    question: str,
) -> list[str]:
    """在同名类或同名方法之间确定本次问题指向的真实文件。

    参数:
        - `project_index`：当前项目完整符号索引，包含类名、文件路径和定义行。
        - `evidence`：首轮通用锚点、精确符号和语义扩展证据，类型为 `list[dict]`。
        - `explicit_symbols`：问题中提取的类、方法或配置标识，类型为 `list[str]`。
        - `question`：用户原始业务问题，类型为 `str`；其中的非类名英文目录词用于
          直接消歧，例如 `PatronCharge` 可以排除另一个 `SettlementService`。

    Returns:
        按证据排序确定的目标类文件列表。问题给出目录词时优先匹配路径；没有目录
        词时比较各候选与高排名精确符号命中的路径重合度。无法唯一判断时保留并列
        候选，不凭空选择其中一个同名实现。
    """
    class_symbols = [
        (str(symbol.get("symbol_name") or ""), str(symbol.get("file") or ""))
        for symbol in project_index.get("symbols") or []
        if str(symbol.get("symbol_type") or "") in {"class", "interface", "trait"}
        and symbol.get("symbol_name")
        and symbol.get("file")
    ]
    if not class_symbols or not explicit_symbols:
        return []
    requested_classes = {
        name
        for name, _ in class_symbols
        if name in explicit_symbols
        or any(symbol.startswith(f"{name}::") for symbol in explicit_symbols)
    }
    candidates = sorted({file_path for name, file_path in class_symbols if name in requested_classes})
    if len(candidates) <= 1:
        return candidates

    common_tokens = {
        "API", "HTTP", "HTTPS", "ID", "JSON", "PHP", "REDIS", "SQL", "URL",
        "UUID", "YAML",
    }
    scope_terms = [
        value
        for value in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", question)
        if value not in requested_classes and value.upper() not in common_tokens
    ]
    if scope_terms:
        scoped = [
            file_path
            for file_path in candidates
            if any(term.casefold() in file_path.casefold() for term in scope_terms)
        ]
        if scoped:
            return scoped

    candidate_tokens = {
        file_path: _path_scope_tokens(file_path)
        for file_path in candidates
    }
    scores = {file_path: 0 for file_path in candidates}
    for order, item in enumerate(evidence):
        file_path = str(item.get("file") or "")
        is_explicit = item.get("retrieval_stage") == "explicit_symbol" or any(
            str(item.get("query") or "").startswith(f"{name}::") or item.get("query") == name
            for name in requested_classes
        )
        if not file_path or not is_explicit:
            continue
        try:
            rank = int(item.get("generic_rank", order))
        except (TypeError, ValueError):
            rank = order
        weight = max(1, 80 - rank)
        hit_tokens = _path_scope_tokens(file_path)
        for candidate in candidates:
            if candidate == file_path:
                scores[candidate] += weight * 8
                continue
            scores[candidate] += len(candidate_tokens[candidate].intersection(hit_tokens)) * weight
    highest = max(scores.values(), default=0)
    if highest <= 0:
        return candidates
    return [file_path for file_path in candidates if scores[file_path] == highest]


def _focus_explicit_target_evidence(
    evidence: list[dict],
    target_files: list[str],
    explicit_symbols: list[str],
) -> list[dict]:
    """移除其他模块中与目标类同名的证据，同时保留配置和调用链旁证。

    参数:
        - `evidence`：已排序的诊断证据，类型为 `list[dict]`。
        - `target_files`：`_select_explicit_target_files` 确认的目标类文件。
        - `explicit_symbols`：问题中的显式标识，用于识别需要消歧的类文件名。

    Returns:
        目标文件仍处于靠前位置，且其他模块同名类文件被排除的证据列表。不同文件名
        的调用方、配置定义和辅助服务不会被删除。
    """
    if not target_files:
        return evidence
    target_set = set(target_files)
    target_stems = {
        file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
        for file_path in target_files
    }
    requested_stems = {
        value.split("::", 1)[0].casefold()
        for value in explicit_symbols
        if value
    }
    conflicting_stems = target_stems.intersection(requested_stems)
    focused = []
    for item in evidence:
        file_path = str(item.get("file") or "")
        normalized = file_path.replace("\\", "/")
        stem = normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
        if stem in conflicting_stems and file_path not in target_set:
            continue
        focused.append(item)
    focused.sort(key=lambda item: (0 if str(item.get("file") or "") in target_set else 1))
    return focused


def _inherited_class_evidence(
    root: str,
    project: str,
    project_index: dict,
    target_files: list[str],
    limit: int = 1,
) -> list[dict]:
    """沿显式目标类的 `extends` 声明补充直接基类定义证据。

    参数:
        - `root`：所有项目仓库的白名单根目录，类型为 `str`。
        - `project`：当前诊断项目标识，类型为 `str`。
        - `project_index`：当前项目完整符号索引，包含类名、文件和定义行。
        - `target_files`：问题显式指向的类文件，类型为 `list[str]`。
        - `limit`：最多补充的基类层数和文件数，类型为 `int`；默认只补充直接
          基类，避免通用基础设施父类把业务类事实挤出证据窗口。

    Returns:
        每个直接基类的一条类定义证据，并携带 `hierarchy_of` 和
        `hierarchy_depth` 供最终证据预算识别。父类同名定义无法唯一判断或源文件
        读取失败时停止该分支，不猜测同名类的模块归属。
    """
    if limit < 1 or limit > 12:
        raise ValueError("继承层级证据数量必须在 1 到 12 之间")
    class_symbols = [
        symbol
        for symbol in project_index.get("symbols") or []
        if str(symbol.get("symbol_type") or "") in {"class", "interface", "trait"}
        and symbol.get("symbol_name")
        and symbol.get("file")
    ]
    by_name: dict[str, list[dict]] = {}
    for symbol in class_symbols:
        by_name.setdefault(str(symbol["symbol_name"]), []).append(symbol)

    evidence = []
    seen_files = set()
    queue = [(file_name, 0, "") for file_name in target_files]
    while queue and len(evidence) < limit:
        current_file, depth, child_name = queue.pop(0)
        if not current_file or depth >= limit or current_file in seen_files:
            continue
        seen_files.add(current_file)
        try:
            context = read_file(root, project, current_file, 1, 160)
        except CodeToolError:
            continue
        parent_match = _PARENT_CLASS_PATTERN.search(str(context.get("content") or ""))
        if parent_match is None:
            continue
        parent_name = parent_match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
        candidates = by_name.get(parent_name) or []
        if not candidates:
            continue
        child_repository = current_file.replace("\\", "/").split("/", 1)[0]
        same_repository = [
            item
            for item in candidates
            if str(item.get("file") or "").replace("\\", "/").split("/", 1)[0]
            == child_repository
        ]
        selected_pool = same_repository or candidates
        selected_files = {str(item.get("file") or "") for item in selected_pool}
        if len(selected_files) != 1:
            continue
        parent_symbol = selected_pool[0]
        parent_file = str(parent_symbol.get("file") or "")
        parent_line = int(parent_symbol.get("line") or 0)
        if not parent_file or parent_line < 1:
            continue
        evidence.append(
            {
                "type": "symbol",
                "query": parent_name,
                "file": parent_file,
                "line": parent_line,
                "content": f"{parent_name} 父类定义",
                "hierarchy_of": child_name or current_file,
                "hierarchy_depth": depth + 1,
            }
        )
        queue.append((parent_file, depth + 1, parent_name))
    return evidence


def _merge_semantic_hits(*groups: list[dict]) -> list[dict]:
    """合并两轮语义检索结果并保留同一行的全部命中依据。

    参数:
        - `*groups`：按检索轮次分组的通用命中，每组类型为 `list[dict]`；每项
          至少包含项目内文件路径和行号，可能包含 `matched_queries` 与
          `matched_concepts`。

    Returns:
        按 `(文件, 行)` 去重后的命中。相同行会合并两轮命中的业务词和概念，
        使后续排序能看到完整覆盖度，而不是被后一轮结果覆盖。
    """
    merged: list[dict] = []
    positions = {}
    for group in groups:
        for item in group:
            key = (str(item.get("file") or ""), int(item.get("line") or 0))
            if key not in positions:
                positions[key] = len(merged)
                copy = dict(item)
                copy["matched_queries"] = list(dict.fromkeys(copy.get("matched_queries") or []))
                copy["matched_concepts"] = list(dict.fromkeys(copy.get("matched_concepts") or []))
                merged.append(copy)
                continue
            existing = merged[positions[key]]
            existing["matched_queries"] = list(
                dict.fromkeys([*(existing.get("matched_queries") or []), *(item.get("matched_queries") or [])])
            )
            existing["matched_concepts"] = list(
                dict.fromkeys([*(existing.get("matched_concepts") or []), *(item.get("matched_concepts") or [])])
            )
    return merged


def _evidence_relevance(item: dict) -> tuple:
    """为已命中的代码证据生成稳定排序键，优先保留业务源码。

    参数:
        - `item`：代码检索命中项，包含文件、行号和扩展后的源码上下文，类型为
          `dict`。

    Returns:
        可直接用于升序排序的元组。生产 PHP 源码优先于测试、语言包和文档；
        控制流程、异常、Redis 风控和返回语句比普通注释更容易解释业务限制。
    """
    file_name = str(item.get("file") or "").lower()
    context = str(item.get("context") or "").lower()
    source_text = context or str(item.get("content") or "").lower()
    score = 0
    if "/php/" in f"/{file_name}" and "/phpunit/" not in f"/{file_name}":
        score += 8
    # waky 系列活动业务并不都放在 `php/services` 下。真实生产实现常位于
    # `waky6_miyouactivity/crontab`、`action_activity` 和 `Logic`，这些目录
    # 必须比旧年报告、后台页面和普通基础库更早进入证据窗口。
    if "miyouactivity/crontab/" in f"/{file_name}":
        score += 14
    elif "miyouactivity/action_activity/" in f"/{file_name}":
        score += 11
    elif "miyouactivity/logic/" in f"/{file_name}" or "miyouactivity/config/" in f"/{file_name}":
        score += 9
    if "/reports/" in f"/{file_name}":
        score -= 5
    if "/phpunit/" in f"/{file_name}" or "/tests/" in f"/{file_name}":
        score -= 3
    if "/lang/" in f"/{file_name}":
        score -= 1
    if "/lang/zh/" in f"/{file_name}":
        # 中文提示是业务限制的直接可观测结果，保留代表性文案供报告和 LLM
        # 使用；其他语言包仍只作为低优先级辅助证据。
        score += 6
    if item.get("type") == "generic_code":
        # 通用片段只负责发现业务位置，精确符号和配置命中的证据应优先进入报告。
        score -= 2
        matched_terms = [str(value) for value in item.get("matched_queries") or [] if str(value)]
        longest_term = max((len(value) for value in matched_terms), default=0)
        if longest_term >= 4:
            # 四字片段通常是“商品碎片”“年度金主”等真实业务对象，保留其上下文
            # 才能把等级配置和公式一起送入确定性分析。
            score += 8
        elif longest_term <= 2:
            # “活动”“年度”这类两字泛词在大型活动中普遍存在，只适合作为线索，
            # 不能让某个巨型基础脚本仅凭大量命中挤占八十条证据名额。
            score -= 8
    retrieval_stage = str(item.get("retrieval_stage") or "")
    matched_queries = [str(value) for value in item.get("matched_queries") or [] if str(value)]
    longest_query = max((len(value) for value in matched_queries), default=0)
    if retrieval_stage == "business_anchor" and longest_query >= 4:
        # 问题原句直接命中生产源码注释，是比跨语言概念扩展强得多的证据。
        # 先保留这些行可以防止同义英文词在大型仓库中占满证据窗口。
        score += 45
    elif retrieval_stage == "business_anchor":
        # “用户”“页面”等两字锚点过于宽泛，只保留为低优先级候选。
        score -= 6
    if retrieval_stage == "semantic_vector" and item.get("semantic_vector") is True:
        # 真实向量候选没有中文字面重合，不能套用通用词命中惩罚。强相似度候选
        # 获得更高预留权重，弱候选仍只作为符号发现的辅助线索。
        try:
            vector_score = max(0.0, min(1.0, float(item.get("vector_score") or 0.0)))
        except (TypeError, ValueError):
            vector_score = 0.0
        score += 38 + int(vector_score * 30)
        if item.get("semantic_vector_strong") is True:
            score += 18
    if item.get("type") == "config_definition":
        # 配置定义行给出调用点无法提供的真实数值。动态发现配置键后必须优先回到
        # 定义处读取，否则报告只能证明“某段代码读取了阈值”，不能回答阈值是多少。
        score += 34
    if item.get("type") == "version_parser_code":
        # 历史快照只带出 `MIN_VERSION` / `MIN_BUILD` 等门槛常量时，版本展示规则
        # 位于另一个客户端解析函数。该证据由历史符号动态定位，必须优先保留，
        # 否则大型仓库的通用命中会把函数体挤出上下文，最终只能报告整数门槛而
        # 无法还原用户实际看到的版本号。
        score += 42
    if item.get("generic_rank") is not None:
        try:
            generic_rank = max(0, int(item.get("generic_rank")))
        except (TypeError, ValueError):
            generic_rank = 0
        # 通用命中在进入 Worker 前已按“稀有业务词、概念覆盖和源码可信度”
        # 排过序。后续证据去重和相关性排序必须保留这个顺序，否则四个等级配置
        # 会被大量同名的活动标题重新淹没。
        score += max(0, 36 - generic_rank)
    # 概念覆盖是跨业务、跨项目通用的证据信号：同一行或同一符号同时涉及
    # “钻石 + 档位”“提现 + 手续费”等两个以上概念时，比只出现“展示”“用户”
    # 的泛化命中更可能承载当前问题需要的业务链路。这里不绑定任何具体类名。
    matched_concepts = [str(value) for value in item.get("matched_concepts") or [] if str(value)]
    score += len(matched_concepts) * 8
    if len(matched_concepts) >= 2:
        score += 12
    query = str(item.get("query") or "").casefold()
    if item.get("type") == "symbol":
        # 符号索引补全的是类/方法声明本身，不是跨文件调用点。真实年度活动
        # 仓库中同一个类名可能在 `require_file.php` 被引用多次，声明证据必须
        # 优先进入窗口，才能读取完整规则实现而不是只看到引入路径。
        score += 15
    if "::" in query:
        # 规划器生成的 `Class::method` 是已由符号索引确认的方法定义检索。它比
        # 类名在多个入口中的普通引用更能解释实际执行规则，应优先保留其完整方法体。
        score += 10
    if query and f"function {query}" in source_text:
        # 叶子方法名检索可能没有 `Class::` 前缀。真正命中的定义行包含
        # `function ordinaryPrizes` 等文本，提升它可以优先保留公式方法体。
        score += 10
    if item.get("call_chain_entry"):
        try:
            depth = max(0, int(item.get("call_chain_depth") or 0))
        except (TypeError, ValueError):
            depth = 0
        # 静态调用链按广度展开。入口和最近一层通常包含金额校验、扣款和转账，
        # 深层节点更容易落到日志、缓存或框架辅助方法；按深度衰减可让最终八条
        # 调用链证据优先解释真实业务路径。
        score += max(0, 24 - depth * 4)
    # 以下信号描述的是业务限制通常呈现的代码结构，而不是某个项目的具体实现。
    # 这类结构比普通赋值和注释更容易解释阻断、降级和金融计算结果。
    for signal, weight in (
        ("application::stop", 4),
        ("throw new", 4),
        ("return false", 2),
        ("return [false", 3),
        ("does not support", 3),
        ("exceptionguard", 2),
    ):
        if signal in context:
            score += weight
    return (-score, file_name, int(item.get("line") or 0))


def _method_evidence_range(
    project_index: dict,
    item: dict,
    max_span: int = 320,
) -> tuple[int, int]:
    """为命中方法定义的证据确定完整方法体行范围。

    参数:
        - `project_index`：当前项目的 PHP 符号索引，包含方法名、文件路径和
          定义行号，类型为 `dict`。
        - `item`：当前代码命中项，`query` 可能是 `Class::method`，`file` 和
          `line` 表示符号搜索命中的位置，类型为 `dict`。
        - `max_span`：单个方法最多扩展的行数，类型为 `int`；用于防止异常的超大
          方法无限放大证据和模型上下文。

    Returns:
        `(start_line, end_line)`。只有查询文本与索引中的方法符号精确一致且命中
        行与定义行相差不超过 2 时才扩展；其他普通搜索仍使用调用方原有的邻行窗口。
    """
    query = str(item.get("query") or "")
    file_name = str(item.get("file") or "")
    hit_line = int(item.get("line") or 0)
    if hit_line < 1:
        return 1, 12
    if item.get("type") == "version_parser_code":
        # 此类证据由历史符号定位到方法声明，但关键编码动作可能位于同方法的
        # Android/iOS 分支后半段。固定前向窗口按历史助手采集范围读取，避免
        # 紧邻的下一个方法声明把实际 `intval($parts[0] . $parts[1])` 截掉。
        return max(1, hit_line - 4), hit_line + 180
    if item.get("retrieval_stage") == "semantic_vector":
        # 向量索引以最多四千字符为一块，业务方法可能位于分块中段。读取完整
        # 分块行范围，才能让确定性分析看到相似度实际对应的配置和分支，而不是
        # 只读取分块首行附近的无关类声明。
        line_end = max(hit_line, int(item.get("line_end") or hit_line))
        return hit_line, min(hit_line + max_span, line_end)
    if "." in query and "::" not in query:
        # 点号配置键的命中通常位于实际执行校验的方法体内，而不是配置定义文件。
        # 读取命中行前后 24 行可以同时带回方法注释、业务说明和紧随其后的计数
        # 调用；窗口仍受单次文件读取上限约束，不扩大到整份源码。
        return max(1, hit_line - 24), hit_line + 24
    file_symbols = sorted(
        (
            symbol
            for symbol in project_index.get("symbols") or []
            if str(symbol.get("file") or "") == file_name
        ),
        key=lambda symbol: int(symbol.get("line") or 0),
    )
    matching_symbol = None
    for symbol in file_symbols:
        symbol_line = int(symbol.get("line") or 0)
        symbol_name = str(symbol.get("symbol_name") or "")
        leaf_name = symbol_name.rsplit("::", 1)[-1]
        query_matches_symbol = query == symbol_name or ("::" not in query and query == leaf_name)
        if (
            str(symbol.get("symbol_type") or "") in {"class", "interface", "trait"}
            and query == symbol_name
            and abs(symbol_line - hit_line) <= 2
        ):
            # 小服务类通常只有几十到两百行，完整读取类体比固定十二行窗口更能
            # 同时保留公开方法、私有辅助方法和关键公式；read_file 会自动截到文件尾。
            return 1, max_span
        if (
            str(symbol.get("symbol_type") or "") == "method"
            and query_matches_symbol
            and abs(symbol_line - hit_line) <= 2
        ):
            matching_symbol = symbol
            break
    if matching_symbol is None:
        matched_terms = [str(value) for value in item.get("matched_queries") or [] if str(value)]
        if item.get("type") == "generic_code" and (
            max((len(value) for value in matched_terms), default=0) >= 4
            or len(item.get("matched_concepts") or []) >= 2
        ):
            # 四字业务片段所在声明块可能包含多层配置或一个短方法的完整实现。
            # 扩到前后 60 行只作用于少量高可信通用命中，便于同时看到说明、字段
            # 和辅助方法；普通 1/2 字泛词仍维持 12 行的小窗口。
            return max(1, hit_line - 60), hit_line + 60
        return max(1, hit_line - 12), hit_line + 12
    symbol_line = int(matching_symbol.get("line") or 0)
    next_symbol_line = next(
        (
            int(symbol.get("line") or 0)
            for symbol in file_symbols
            if int(symbol.get("line") or 0) > symbol_line
        ),
        symbol_line + max_span,
    )
    return max(1, symbol_line - 3), min(symbol_line + max_span, max(symbol_line, next_symbol_line - 1))


def _select_diagnostic_evidence(
    evidence: list[dict],
    queries: list[str],
    limit: int = MAX_DIAGNOSTIC_EVIDENCE,
    priority_files: Optional[list[str]] = None,
) -> list[dict]:
    """在证据总量上限内优先保留通用发现和每个检索词的确定性命中。

    参数:
        - `evidence`：已按 `_evidence_relevance` 排序并去重的代码证据，类型为
          `list[dict]`。
        - `queries`：本次领域词、通用片段和符号规划器生成的检索词，类型为
          `list[str]`；用于保证关键方法不会因同一目录文件过多而被截断。
        - `limit`：最终进入上下文扩展的证据条数，类型为 `int`。
        - `priority_files`：问题显式指向的类文件，类型为 `list[str] | None`；
          这些文件中的类声明和规则实现会先进入证据窗口。

    Returns:
        保持原有相关度顺序的去重证据列表。先保留高可信通用片段以覆盖业务配置，
        再为每个检索词保留第一条确定性命中，最后按相关度补足剩余名额。
    """
    if limit < 1:
        raise ValueError("诊断证据上限必须大于 0")
    selected: list[dict] = []
    selected_keys = set()

    def append(item: dict) -> None:
        """按文件和行号去重追加一条证据。

        参数:
            - `item`：待加入最终证据集合的代码命中，类型为 `dict`。
        """
        key = (str(item.get("file") or ""), int(item.get("line") or 0))
        if key not in selected_keys and len(selected) < limit:
            selected_keys.add(key)
            selected.append(item)

    # 通用业务片段是未知领域问题的主要发现入口，先保留最多 28 条。真实活动
    # 配置常用四个以上等级段表达同一规则，二十条会在最后一个等级和随机发奖
    # 方法之前截断；二十八条仍低于四十八条总预算，不会无限膨胀。
    generic_count = 0
    generic_budget = 28
    normalized_queries = list(
        dict.fromkeys(str(query) for query in queries if str(query))
    )

    def append_generic(item: dict) -> bool:
        """按上限追加一条通用证据并返回是否实际加入。

        参数:
            - `item`：待检查的通用源码命中，类型为 `dict`。

        Returns:
            已加入新的文件和行号时返回 `True`；重复或预算已满时返回 `False`。
        """
        nonlocal generic_count
        if item.get("retrieval_stage") == "semantic_vector":
            # 真实向量候选由独立的小预算处理，不能顺带占满中文锚点和概念
            # 命中共用的通用候选额度。
            return False
        key = (str(item.get("file") or ""), int(item.get("line") or 0))
        if key in selected_keys or generic_count >= generic_budget:
            return False
        append(item)
        generic_count += 1
        return True

    # 显式目标类文件是回答“这个类具体做什么”的主证据。先保留其中的类声明、
    # 条件和业务动作，再由通用片段补充配置与跨文件旁证；同名类或泛化文案不会
    # 再把真正实现挤出最终证据窗口。
    priority_file_set = set(priority_files or [])
    priority_count = 0
    for item in evidence:
        if str(item.get("file") or "") not in priority_file_set:
            continue
        append(item)
        priority_count += 1
        if priority_count >= 24 or len(selected) >= limit:
            break

    # 直接基类可能承载模板方法、保留期和实际删除/过期动作。它只占少量独立
    # 预算，既不会替代显式目标类，也不会因为父类文件没有命中原始问题词而丢失。
    hierarchy_count = 0
    for item in evidence:
        if not item.get("hierarchy_of"):
            continue
        append(item)
        hierarchy_count += 1
        if hierarchy_count >= 6 or len(selected) >= limit:
            break

    # 真实向量候选没有中文锚点，但可能是未知业务唯一可用的发现入口。单独预留
    # 最多十条源码证据，强候选优先；它们只负责把真实文件和方法带进后续源码
    # 校验，最终事实仍由读取到的控制流、配置定义和调用链决定。
    semantic_vector_count = 0
    for item in evidence:
        if (
            item.get("retrieval_stage") != "semantic_vector"
            or item.get("semantic_vector") is not True
        ):
            continue
        append(item)
        semantic_vector_count += 1
        if semantic_vector_count >= 10 or len(selected) >= limit:
            break

    # 历史版本问题需要把提交快照中的门槛常量和当前源码中的解析函数组合起来。
    # 解析函数证据由历史符号通用定位且最多一条，单独预留名额，避免被通用业务
    # 候选占满后丢失，保证确定性报告仍能还原实际客户端版本格式。
    for item in evidence:
        if item.get("type") != "version_parser_code":
            continue
        append(item)
        break

    # 概念英文词命中的配置文件（如 noble → newNobleLv.php）需要独立名额。
    # 全文检索会因同词文件过多在截断处丢掉定义文件，而这类证据没有业务锚点
    # 和 matched_queries，不能依赖后续通用候选回填。
    concept_config_count = 0
    for item in evidence:
        if item.get("type") != "concept_config":
            continue
        append(item)
        concept_config_count += 1
        if concept_config_count >= 3 or len(selected) >= limit:
            break

    # 第一层保留问题原句直接命中的四字以上业务锚点，最多十八条。等级配置、
    # 页面入口和业务注释往往都落在这里，优先级高于跨语言概念候选。
    for item in evidence:
        if item.get("type") != "generic_code":
            continue
        longest_term = max(
            (len(str(value)) for value in item.get("matched_queries") or []),
            default=0,
        )
        if item.get("retrieval_stage") == "business_anchor" and longest_term >= 4:
            append_generic(item)
        if generic_count >= 18 or len(selected) >= limit:
            break

    # 第二层从剩余通用命中里选择同时覆盖两个以上业务概念的实现代码。这样
    # “依赖分配”“保底池”“扣款转账”等泛化标题命中较少的关键方法，不会在
    # 十八个高频标题锚点之后被排除。
    for item in evidence:
        if item.get("type") != "generic_code":
            continue
        if len(item.get("matched_concepts") or []) >= 2:
            append_generic(item)
        if generic_count >= generic_budget or len(selected) >= limit:
            break

    # 第三层只补普通通用候选到总预算，不再允许后续回填绕过该预算。
    for item in evidence:
        if item.get("type") != "generic_code":
            continue
        append_generic(item)
        if generic_count >= generic_budget or len(selected) >= limit:
            break

    # 配置定义行必须在通用候选之后、普通引用之前进入证据窗口。否则像
    # `anchor.diamond_to_usd` 这样的完整键只会保留调用点，无法带回真实比例。
    # 点号配置键的末级字段优先于其他配置定义，避免 `coin_price` 等无关配置
    # 占满预算后，把当前问题真正读取的 `min_diamonds` / `dollar_to_diamond`
    # 定义挤出证据窗口。
    priority_config_terms = list(
        dict.fromkeys(
            query.rsplit(".", 1)[-1]
            for query in normalized_queries
            if "." in query and query.rsplit(".", 1)[-1]
        )
    )
    config_definition_count = 0
    for term in priority_config_terms:
        term_count = 0
        for item in evidence:
            if item.get("type") != "config_definition":
                continue
            if term not in {
                str(item.get("query") or ""),
                *(str(value) for value in item.get("matched_queries") or []),
            }:
                continue
            before = len(selected)
            append(item)
            if len(selected) > before:
                config_definition_count += 1
                term_count += 1
            if term_count >= 3 or config_definition_count >= 16 or len(selected) >= limit:
                break
        if config_definition_count >= 16 or len(selected) >= limit:
            break
    for item in evidence:
        if item.get("type") != "config_definition":
            continue
        append(item)
        config_definition_count = min(16, config_definition_count + 1)
        if config_definition_count >= 16 or len(selected) >= limit:
            break
    if priority_config_terms:
        # 环境选择函数本身不是配置定义，但解释 `getByTestMode(测试值, 正式值)`
        # 的环境顺序不可缺少。它必须在调用链和通用符号填满预算前单独保留。
        test_mode_helper = next(
            (
                item
                for item in evidence
                if str(item.get("query") or "") == "function getByTestMode"
                or "function getByTestMode" in (item.get("matched_queries") or [])
            ),
            None,
        )
        if test_mode_helper is not None:
            append(test_mode_helper)

    # 直接调用链节点解释“入口之后执行了哪些校验和扣款”，不能只靠通用词重合
    # 竞争剩余名额。预留少量已索引方法声明，随后读取完整方法体；上限保持较小，
    # 避免认证、日志和基础库节点顺着静态调用图挤占业务证据。
    call_chain_groups = {}
    for item in evidence:
        if item.get("type") != "symbol" or not item.get("call_chain_entry"):
            continue
        chain_order = int(item.get("call_chain_order") or 0)
        call_chain_groups.setdefault(chain_order, []).append(item)
    ordered_call_chain_groups = [
        call_chain_groups[order]
        for order in sorted(call_chain_groups)
    ]
    for group in ordered_call_chain_groups:
        group.sort(key=lambda candidate: int(candidate.get("call_chain_depth") or 0))
    call_chain_count = 0
    # 只对相关性最高的四条入口链路各保留入口、第一层和第一层业务子调用。
    # 调用链多出的第十二条之后仍是配置、类定义和跨业务候选的空间；相比让
    # 八条入口各占两个名额，这样能同时保留“校验”和“扣款/转账”两侧证据。
    for depth_index in range(3):
        for group in ordered_call_chain_groups[:4]:
            if depth_index >= len(group):
                continue
            append(group[depth_index])
            call_chain_count += 1
            if call_chain_count >= 12 or len(selected) >= limit:
                break
        if call_chain_count >= 12 or len(selected) >= limit:
            break

    # 每个规划检索词至少保留一条最佳命中。符号定义优先于同名引用，方法定义
    # 再依靠函数定义权重排在同 query 的调用点之前，从而带回完整公式和业务规则。
    selection_queries = list(normalized_queries)
    if priority_config_terms:
        # 配置值出现环境覆盖函数时，函数实现也不在原始问题词里。配置定义已进入
        # 候选后补一个框架级查询词，确保选择阶段不会把此前定向检索到的实现丢掉。
        selection_queries.append("function getByTestMode")
    for query in selection_queries:
        config_definition = next(
            (
                item
                for item in evidence
                if item.get("type") == "config_definition"
                and (
                    str(item.get("query") or "") == query
                    or query in (item.get("matched_queries") or [])
                )
            ),
            None,
        )
        if config_definition is not None:
            append(config_definition)
            continue
        symbol_definition = next(
            (
                item
                for item in evidence
                if item.get("type") == "symbol" and str(item.get("query") or "") == query
            ),
            None,
        )
        if symbol_definition is not None:
            append(symbol_definition)
            continue
        for item in evidence:
            if (
                str(item.get("query") or "") == query
                or query in (item.get("matched_queries") or [])
            ):
                append(item)
                break

    for item in evidence:
        if item.get("retrieval_stage") == "semantic_vector":
            # 向量候选已在独立预算中处理，普通回填不能再次追加超出上限。
            continue
        if item.get("type") == "generic_code" and generic_count >= generic_budget:
            continue
        append(item)
        if len(selected) >= limit:
            break
    return selected


def _deduplicate_evidence(evidence: list[dict]) -> list[dict]:
    """按文件和行号合并代码命中，并优先保留符号定义语义。

    参数:
        - `evidence`：文本搜索和符号索引合并前的证据，类型为 `list[dict]`。
          每项至少包含项目内相对文件路径和命中行号。

    Returns:
        保持首次出现顺序的去重证据。同一行若同时被文本搜索和符号索引命中，
        用 `symbol` 项替换普通 `code` 项，确保后续按查询词选择时能识别这是
        类或方法声明，而不是恰好位于同一行的引用或说明文字。替换时会合并
        两边的业务概念和命中词，避免丢失跨语言检索形成的覆盖度。
    """
    deduplicated: list[dict] = []
    positions = {}
    for item in evidence:
        key = (str(item.get("file") or ""), int(item.get("line") or 0))
        existing_position = positions.get(key)
        if existing_position is None:
            positions[key] = len(deduplicated)
            deduplicated.append(item)
            continue
        existing = deduplicated[existing_position]
        if item.get("type") == "symbol" and existing.get("type") != "symbol":
            merged = dict(item)
            merged["matched_queries"] = list(
                dict.fromkeys([*(existing.get("matched_queries") or []), *(item.get("matched_queries") or [])])
            )
            merged["matched_concepts"] = list(
                dict.fromkeys([*(existing.get("matched_concepts") or []), *(item.get("matched_concepts") or [])])
            )
            if not merged["matched_queries"]:
                merged.pop("matched_queries")
            if not merged["matched_concepts"]:
                merged.pop("matched_concepts")
            deduplicated[existing_position] = merged
        elif item.get("type") != "symbol":
            existing["matched_queries"] = list(
                dict.fromkeys([*(existing.get("matched_queries") or []), *(item.get("matched_queries") or [])])
            )
            existing["matched_concepts"] = list(
                dict.fromkeys([*(existing.get("matched_concepts") or []), *(item.get("matched_concepts") or [])])
            )
    return deduplicated


def _symbol_evidence(project_index: dict, queries: list[str]) -> list[dict]:
    """把精确类名和 `Class::method` 转换为符号定义证据。

    参数:
        - `project_index`：当前 PHP 符号索引，包含类、方法和定义行号，类型为
          `dict`；索引来自当前项目只读扫描。
        - `queries`：领域词与动态发现检索词，类型为 `list[str]`。

    Returns:
        每个在索引中精确存在的类、接口、Trait 或 `Class::method` 方法定义对应
        一条证据。文本搜索可能先命中跨文件引用，无法保证取得声明，因此需要
        符号索引补上定义行；文件路径和行号仍会通过受控 `read_file` 再验证，
        不能仅凭索引生成业务结论。
    """
    query_set = {str(query) for query in queries if str(query)}
    if not query_set:
        return []
    evidence = []
    seen = set()
    for symbol in project_index.get("symbols") or []:
        symbol_type = str(symbol.get("symbol_type") or "")
        if symbol_type not in {"class", "interface", "trait", "method"}:
            continue
        symbol_name = str(symbol.get("symbol_name") or "")
        file_name = str(symbol.get("file") or "")
        line_number = int(symbol.get("line") or 0)
        if symbol_name not in query_set or not file_name or line_number < 1:
            continue
        key = (file_name, line_number)
        if key in seen:
            continue
        seen.add(key)
        symbol_label = {
            "class": "类定义",
            "interface": "接口定义",
            "trait": "Trait 定义",
            "method": "方法定义",
        }[symbol_type]
        evidence.append(
            {
                "type": "symbol",
                "query": symbol_name,
                "file": file_name,
                "line": line_number,
                "content": f"{symbol_name} {symbol_label}",
            }
        )
    return evidence


def _is_config_definition_match(query: str, match: dict) -> bool:
    """判断检索结果是否是对应配置键的真实定义行。

    参数:
        - `query`：配置规划器提取的末级字段名，例如 `diamond_to_usd`，类型为
          `str`。完整点号键通常出现在业务调用点，不能直接当作定义词。
        - `match`：`search_code` 返回的单个文件命中，包含项目内路径和当行文本，
          类型为 `dict`。

    Returns:
        文件位于配置目录、当行包含同名引号字段且使用 PHP 数组赋值符时返回
        `True`。该判断只负责把候选标记为配置定义，实际数值仍由后续
        `read_file` 和确定性分析读取。
    """
    term = str(query or "").strip()
    if not term:
        return False
    normalized_file = str(match.get("file") or "").replace("\\", "/").casefold()
    file_name = f"/{normalized_file}"
    if "/config/" not in file_name and "/conf/" not in file_name:
        return False
    content = str(match.get("content") or "")
    if "=>" not in content:
        return False
    return f"'{term}'" in content or f'"{term}"' in content


_CONFIG_ACCESS_PATTERN = re.compile(
    r"\bConfig\s*::\s*get\s*\(\s*(['\"])(?P<key>[A-Za-z_][A-Za-z0-9_.]*)\1"
)


def _config_keys_from_hits(hits: list[dict], limit: int = 12) -> list[str]:
    """从已召回源码及其上下文中提取真实读取的 PHP 配置键。

    参数:
        - `hits`：首轮通用检索和概念检索命中，类型为 `list[dict]`。每项可包含
          命中行 `content` 与相邻源码 `context`；配置读取通常位于日志命中的前
          一行，因此两者都必须扫描。
        - `limit`：最多返回的配置键数量，类型为 `int`。限制用于避免大型仓库中
          的配置读取占满后续精确检索预算。

    Returns:
        按源码出现顺序去重的完整点号配置键。这里只提取 `Config::get()` 的参数，
        不会把同名数组字段或普通字符串当成配置，也不能证明配置最终值；调用方
        仍需搜索定义文件并读取真实数字或布尔值。
    """
    if limit < 1 or limit > 40:
        raise ValueError("配置键数量必须在 1 到 40 之间")
    keys: list[str] = []
    for hit in hits:
        source = "\n".join(
            str(hit.get(field) or "") for field in ("content", "context")
        )
        for match in _CONFIG_ACCESS_PATTERN.finditer(source):
            key = match.group("key")
            if key not in keys:
                keys.append(key)
            if len(keys) >= limit:
                return keys
    return keys


def _nearby_hit_contexts(
    root: str,
    project: str,
    hits: list[dict],
    limit: int = 8,
) -> list[dict]:
    """为高排名命中读取命中行附近的只读源码窗口。

    参数:
        - `root`：被诊断仓库的白名单根目录，类型为 `str`。
        - `project`：当前项目标识，类型为 `str`。
        - `hits`：已经按业务相关度排序的首轮命中，类型为 `list[dict]`。
        - `limit`：最多读取的不同命中数量，类型为 `int`。只读取优先候选，避免
          为大型仓库的每条搜索结果额外做文件 I/O。

    Returns:
        命中对象副本列表，每项增加命中行前 4 行到后 12 行的 `context`。读取失败
        的文件会保留原命中并跳过扩展，不能因为一个不可读文件中断整个诊断。
    """
    if limit < 1 or limit > 24:
        raise ValueError("命中上下文数量必须在 1 到 24 之间")
    enriched: list[dict] = []
    for hit in hits[:limit]:
        item = dict(hit)
        file_name = str(item.get("file") or "")
        line_number = int(item.get("line") or 0)
        if file_name and line_number > 0:
            try:
                source = read_file(
                    root,
                    project,
                    file_name,
                    start_line=max(1, line_number - 4),
                    end_line=line_number + 12,
                )
                item["context"] = source.get("content") or ""
            except (CodeToolError, OSError, ValueError):
                pass
        enriched.append(item)
    return enriched


def _history_version_parser_evidence(
    root: str,
    project: str,
    code_terms: list[str],
) -> list[dict]:
    """读取历史实现引用的客户端版本解析函数体，补齐版本展示规则证据。

    参数:
        - `root`：被诊断仓库的白名单根目录，类型为 `str`。
        - `project`：当前项目标识，类型为 `str`。
        - `code_terms`：Git 历史快照带回当前源码的类、方法和常量名称，类型为
          `list[str]`。其中包含 `version` 或 `build` 的小写函数名用于定位版本
          解析入口，不把固定文件名或项目名写进规则。

    Returns:
        最多一条版本解析函数证据，包含函数声明命中行和最多 180 行函数体上下文。
        只有函数体同时出现版本/构建字段与解析动作时才返回，避免把普通版本常量
        当成编码规则。
    """
    accessor_terms = [
        term
        for term in dict.fromkeys(str(value) for value in code_terms)
        if re.search(r"(?:version|build)", term, re.IGNORECASE)
        and term[:1].islower()
    ][:4]
    if not accessor_terms:
        return []
    result = search_code_terms(
        root,
        project,
        accessor_terms,
        max_results=120,
        extensions=[".php", ".inc", ".phtml"],
        case_insensitive=True,
    )
    for hit in result.get("matches") or []:
        file_name = str(hit.get("file") or "")
        line_number = int(hit.get("line") or 0)
        content = str(hit.get("content") or "")
        if not file_name or line_number < 1:
            continue
        declaration_terms = [
            term
            for term in accessor_terms
            if re.search(
                rf"\bfunction\s+{re.escape(term)}\s*\(",
                content,
                re.IGNORECASE,
            )
        ]
        if not declaration_terms:
            continue
        try:
            source = read_file(
                root,
                project,
                file_name,
                start_line=max(1, line_number - 4),
                end_line=line_number + 180,
            )
        except (CodeToolError, OSError, ValueError):
            continue
        context = str(source.get("content") or "")
        has_version_fields = re.search(r"(?:version|build)", context, re.IGNORECASE)
        has_decode_action = re.search(r"\b(?:intval|explode|split)\b", context, re.IGNORECASE)
        if has_version_fields and has_decode_action:
            return [
                {
                    "type": "version_parser_code",
                    "project": project,
                    "query": f"{declaration_terms[0]} 版本编码规则",
                    "retrieval_stage": "history_version_parser",
                    "file": file_name,
                    "line": line_number,
                    "content": content,
                    "context": context,
                    "matched_queries": declaration_terms,
                }
            ]
    return []


def _search_query_evidence(
    root: str,
    project: str,
    queries: list[str],
    config_definition_query_terms: list[str],
) -> list[dict]:
    """一次性检索全部规划词并转换为可审计的代码命中。

    参数:
        - `root`：所有项目仓库的白名单根目录，类型为 `str`。
        - `project`：当前诊断项目标识，类型为 `str`。
        - `queries`：动态发现和配置规划后的完整检索词，类型为 `list[str]`。
        - `config_definition_query_terms`：需要优先识别配置定义行的末级字段名，
          类型为 `list[str]`。

    Returns:
        与旧逐词搜索结构兼容的命中列表。每项保留所有实际命中的检索词，并以
        规划顺序最早的一项作为 `query`；配置定义命中会显式标记类型。全仓批量
        扫描之后会再做一次仅限 `config/conf` 目录的定义补查，避免调用点数量过多
        时配置文件被全局结果上限截掉，导致只能看到“读取了什么键”而看不到值。
    """
    ordered_queries = list(dict.fromkeys(str(query).strip() for query in queries if str(query).strip()))
    if not ordered_queries:
        return []
    query_order = {query: index for index, query in enumerate(ordered_queries)}
    config_query_set = set(config_definition_query_terms)
    search_results = [
        search_code_terms(
            root,
            project,
            ordered_queries,
            max_results=MAX_TERM_SEARCH_HITS,
            # 精确符号和配置检索必须留在源码与配置范围内。业务文档只由前面的通用
            # 发现阶段召回，不能让说明文档在这里替代真实实现证据。
            extensions=[".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json"],
        )
    ]
    config_queries = list(
        dict.fromkeys(
            [
                *[str(value) for value in config_definition_query_terms if str(value)],
                # 配置值常用 `getByTestMode(测试值, 正式值)` 表达环境差异。只读配置
                # 定义仍无法判断两个数字的环境顺序，因此把同项目中的通用选择函数
                # 定义一起补回证据窗口。
                "function getByTestMode",
            ]
        )
    )
    if config_definition_query_terms:
        try:
            search_results.append(
                search_code_terms(
                    root,
                    project,
                    config_queries,
                    max_results=min(160, MAX_TERM_SEARCH_HITS),
                    extensions=[".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json"],
                    include_globs=("**/config/**", "**/conf/**"),
                )
            )
        except CodeToolError:
            # 定向补查失败不能让已经成功的全仓检索一并失效；它在审计中属于降级，
            # 调用方仍可用主检索结果回答其他问题。
            pass

    merged_matches: dict[tuple[str, int], dict] = {}
    targeted_query_order = {
        query: index
        for index, query in enumerate([*ordered_queries, *config_queries])
    }
    for result in search_results:
        for match in result.get("matches") or []:
            key = (str(match.get("file") or ""), int(match.get("line") or 0))
            existing = merged_matches.get(key)
            if existing is None:
                merged_matches[key] = dict(match)
                continue
            existing["matched_queries"] = list(
                dict.fromkeys(
                    [
                        *(existing.get("matched_queries") or []),
                        *(match.get("matched_queries") or []),
                    ]
                )
            )

    evidence = []
    for match in merged_matches.values():
        matched_queries = [
            str(value)
            for value in match.get("matched_queries") or []
            if str(value) in query_order or str(value) in targeted_query_order
        ]
        matched_queries.sort(key=lambda value: targeted_query_order.get(value, 0))
        if not matched_queries:
            continue
        config_matches = [
            query
            for query in matched_queries
            if query in config_query_set and _is_config_definition_match(query, match)
        ]
        evidence.append(
            {
                "type": "config_definition" if config_matches else "code",
                "project": project,
                "query": (config_matches or matched_queries)[0],
                "matched_queries": matched_queries,
                **{key: value for key, value in match.items() if key != "matched_queries"},
            }
        )
    return evidence


def _call_chain_evidence(call_chains: list[dict], limit: int = 48) -> list[dict]:
    """把静态调用链中的业务方法声明转成可继续读取的源码证据。

    参数:
        - `call_chains`：由 `trace_index_call_chain` 生成的调用链列表，类型为
          `list[dict]`；每个节点包含已索引的符号名、文件路径和定义行号。
        - `limit`：最多补充的方法或类定义数量，类型为 `int`。限制总量可避免
          通用认证、日志和框架节点占满最终证据窗口。

    Returns:
        与入口处于同一业务子仓、且不属于框架目录的符号证据。这里只补定义位置，
        后续仍会通过 `read_file` 读取完整方法体验证真实分支和返回值。
    """
    if limit < 1 or limit > 100:
        raise ValueError("调用链证据数量必须在 1 到 100 之间")
    evidence: list[dict] = []
    seen = set()
    for chain_order, chain in enumerate(call_chains):
        nodes = list(chain.get("nodes") or [])
        entry_repository = ""
        for node in nodes:
            entry_file = str(node.get("file") or "").replace("\\", "/").strip("/")
            if entry_file and "/" in entry_file:
                entry_repository = entry_file.split("/", 1)[0]
                break
        for depth, node in enumerate(nodes):
            symbol_name = str(node.get("symbol_name") or "")
            file_name = str(node.get("file") or "").replace("\\", "/").strip("/")
            line_number = int(node.get("line") or 0)
            if not symbol_name or not file_name or line_number < 1:
                continue
            lowered = f"/{file_name.casefold()}"
            if entry_repository and file_name.split("/", 1)[0] != entry_repository:
                continue
            if any(part in lowered for part in ("/lib/", "/lang/", "/vendor/", "/phpunit/", "/tests/")):
                continue
            key = (file_name, line_number, symbol_name)
            if key in seen:
                continue
            seen.add(key)
            evidence.append(
                {
                    "type": "symbol",
                    "query": symbol_name,
                    "file": file_name,
                    "line": line_number,
                    "content": f"{symbol_name} 调用链定义",
                    "call_chain_entry": str(chain.get("entry") or ""),
                    "call_chain_depth": depth,
                    "call_chain_order": chain_order,
                }
            )
            if len(evidence) >= limit:
                return evidence
    return evidence


def validate_worker_timeouts(current_settings) -> None:
    """校验软超时小于硬超时，避免任务配置互相冲突。

    参数:
        - `current_settings`：待校验的当前 Worker 配置。
    """
    pairs = (
        (
            current_settings.diagnosis_task_soft_time_limit_seconds,
            current_settings.diagnosis_task_time_limit_seconds,
        ),
        (
            current_settings.document_index_soft_time_limit_seconds,
            current_settings.document_index_time_limit_seconds,
        ),
    )
    if any(soft < 1 or hard <= soft for soft, hard in pairs):
        raise ValueError("任务软超时必须为正数且小于硬超时")


def should_run_ai(settings, task: dict, available_evidence: int) -> bool:
    """判断当前诊断任务是否应调用 LLM。

    参数:
        - `settings`：当前 Worker 的 LLM 和证据门槛配置。
        - `task`：包含任务级 use_ai 开关的诊断任务记录。
        - `available_evidence`：去重后的代码、符号、配置和 SQL 证据数。
    """
    if not settings.llm_enabled or task.get("use_ai") is False:
        return False
    return available_evidence >= settings.llm_min_evidence_items


def ai_skip_warning(settings, task: dict, available_evidence: int):
    """说明本次诊断未调用 AI 的真实原因。

    参数:
        - `settings`：当前 Worker 的 LLM 开关和最小证据门槛，类型为应用配置。
        - `task`：诊断任务记录，`use_ai=False` 表示用户明确要求跳过模型，
          类型为 `dict`。
        - `available_evidence`：本次可引用的去重证据数量，类型为 `int`。

    Returns:
        需要展示给用户的跳过原因；LLM 未启用或实际应调用 AI 时返回 `None`。
        任务主动关闭 AI 不能被误写成“代码证据不足”，否则会误导用户继续补料。
    """
    if not settings.llm_enabled:
        return None
    if task.get("use_ai") is False:
        return "已按任务设置跳过 AI 诊断叙写，源码确定性分析不受影响"
    if available_evidence < settings.llm_min_evidence_items:
        return "代码证据不足，已跳过 AI 诊断叙写"
    return None


validate_worker_timeouts(settings)


async def execute_diagnosis(task_id: str) -> None:
    """执行单个诊断任务的状态推进、代码检索和证据持久化。

    参数:
        - `task_id`：诊断任务 ID，类型为 `str`。
    """
    diagnosis_started_at = time.perf_counter()
    pool = await create_pool(settings)
    try:
        await update_task(
            pool,
            task_id,
            status="RUNNING",
            progress=10,
            current_step="Worker 已接收任务",
        )
        await record_trace_span(
            pool,
            task_id,
            "stage.started",
            "worker",
            "started",
            payload={"queue": "diagnosis"},
        )
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        if current.get("retry_count", 0) > 0:
            await clear_task_evidence(pool, task_id)
        await update_task(
            pool,
            task_id,
            status="SEARCHING",
            progress=30,
            current_step="正在理解问题并检索真实源码",
        )
        await record_trace_span(
            pool,
            task_id,
            "stage.started",
            "retrieval",
            "started",
            payload={"mode": "question_understanding"},
        )
        retrieval_started_at = time.perf_counter()
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        queries = extract_search_queries(current["question"])
        # 项目名已由任务字段单独限定，继续把 waky6 当作代码关键词只会召回
        # 文档和部署文件；若问题只有项目名，则保留原词以维持可解释性。
        queries = [item for item in queries if item != current["project"]] or queries
        explicit_symbols = _explicit_symbol_queries(queries)
        verified_explicit_symbols: list[str] = []
        explicit_target_files: list[str] = []
        inherited_target_files: list[str] = []
        evidence = []
        warnings = []
        generic_anchors = extract_generic_anchors(current["question"])
        initial_generic_hits: list[dict] = []
        semantic_vector_hits: list[dict] = []
        semantic_vector_summary: dict = {
            "channel": "semantic_vector",
            "status": "not_requested",
            "hit_count": 0,
        }
        semantic_hits: list[dict] = []
        generic_hits: list[dict] = []
        semantic_plan = {
            "concepts": {},
            "concept_match_terms": {},
            "code_terms": [],
        }
        history_intent = is_history_question(current["question"])
        history_events = []
        history_summary = {
            "requested": history_intent,
            "terms": [],
            "repositories": [],
            "event_count": 0,
            "warnings": [],
        }
        preferred_repository = ""
        discovery_plan = {"queries": [], "sources": [], "mode": "dynamic_discovery"}
        config_definition_query_terms: list[str] = []
        project_index = {"symbols": [], "references": [], "configs": [], "sql": []}
        index_ready = False
        index_prepare_summary: dict = {
            "cache_status": "not_requested",
            "cache_wait_ms": 0.0,
            "build_ms": 0.0,
        }
        term_search_summary: dict = {
            "mode": "batch",
            "query_count": 0,
            "match_count": 0,
            "elapsed_ms": 0.0,
        }
        retrieval_channel_summaries: list[dict] = []
        symbol_sync_summary: dict = {"updated": False, "symbol_count": 0}
        try:
            initial_terms = list(dict.fromkeys([*generic_anchors, *explicit_symbols]))
            initial_outcome = await RetrievalEngine(
                [SearchTermsChannel(search_code_terms)]
            ).run(
                RetrievalRequest(
                    root=settings.code_repositories_root,
                    project=current["project"],
                    query=current["question"],
                    limit=MAX_GENERIC_HITS,
                    options={"terms": initial_terms, "max_results": MAX_GENERIC_HITS},
                )
            )
            retrieval_channel_summaries.extend(initial_outcome.channels)
            initial_result = {"matches": initial_outcome.hits}
            initial_generic_hits = rank_discovery_hits(
                [
                    {
                        **item,
                        "retrieval_stage": stage,
                    }
                    for item in initial_result["matches"]
                    if (
                        stage := _tag_initial_retrieval_stage(
                            item,
                            generic_anchors,
                            explicit_symbols,
                        )
                    )
                ],
                limit=MAX_GENERIC_HITS,
            )
            verified_explicit_symbols = [
                symbol
                for symbol in explicit_symbols
                if any(
                    symbol in (item.get("matched_queries") or [])
                    for item in initial_result["matches"]
                )
            ]
            preferred_repository = infer_preferred_repository(initial_generic_hits)
        except (CodeToolError, ValueError) as exc:
            warnings.append(f"业务锚点和显式符号检索降级: {exc}")

        # 中文锚点不是所有仓库的稳定入口。真实 embedding 用问题语义直接检索
        # 已建索引的源码分块，为未知业务提供不依赖中文注释和固定词表的候选。
        # 这里只接收真实模型且相似度达标的 PHP 源码分块；返回结果仍要经过
        # 符号发现、配置定义搜索和当前源码读取，不能把相似度当作业务事实。
        try:
            semantic_vector_result = await search_semantic_vector_candidates(
                pool,
                current["project"],
                current["question"],
                settings,
            )
            semantic_vector_hits = semantic_vector_result.hits
            semantic_vector_summary = semantic_vector_result.summary
            retrieval_channel_summaries.append(semantic_vector_summary)
            warnings.extend(semantic_vector_result.warnings)
            if semantic_vector_hits:
                initial_generic_hits = rank_discovery_hits(
                    [*initial_generic_hits, *semantic_vector_hits],
                    limit=MAX_GENERIC_HITS,
                )
        except Exception:
            semantic_vector_summary = {
                "channel": "semantic_vector",
                "status": "degraded",
                "hit_count": 0,
                "reason": "语义向量检索编排失败",
            }
            retrieval_channel_summaries.append(semantic_vector_summary)
            warnings.append("真实语义向量检索降级: 语义候选编排失败")

        # 问题中的业务名词决定“要找什么”，首轮高质量命中补充代码实际使用的
        # 同义词。这里只生成可组合概念词，不再把完整问句绑定到某个项目符号。
        semantic_plan = build_semantic_query_plan(
            current["question"],
            seed_hits=initial_generic_hits,
            max_code_terms=24,
        )
        concept_match_terms = semantic_plan.get("concept_match_terms") or {}
        initial_generic_hits = annotate_hits_with_concepts(
            initial_generic_hits,
            concept_match_terms,
        )
        semantic_terms = list(semantic_plan.get("code_terms") or [])
        if semantic_terms:
            try:
                semantic_outcome = await RetrievalEngine(
                    [SearchTermsChannel(search_code_terms)]
                ).run(
                    RetrievalRequest(
                        root=settings.code_repositories_root,
                        project=current["project"],
                        query=current["question"],
                        limit=MAX_GENERIC_HITS,
                        options={
                            "terms": semantic_terms,
                            "max_results": MAX_GENERIC_HITS,
                            "case_insensitive": True,
                        },
                    )
                )
                retrieval_channel_summaries.extend(semantic_outcome.channels)
                semantic_result = {"matches": semantic_outcome.hits}
                semantic_hits = annotate_hits_with_concepts(
                    [
                        {**item, "retrieval_stage": "concept_expansion"}
                        for item in semantic_result["matches"]
                    ],
                    concept_match_terms,
                )
            except (CodeToolError, ValueError) as exc:
                warnings.append(f"跨语言概念检索降级: {exc}")

        merged_generic_hits = _merge_semantic_hits(initial_generic_hits, semantic_hits)
        ranked_generic_hits = rank_discovery_hits(
            merged_generic_hits,
            limit=MAX_GENERIC_HITS,
            preferred_repository=preferred_repository,
        )
        if not preferred_repository:
            preferred_repository = infer_preferred_repository(ranked_generic_hits)
        support = assess_retrieval_support(
            ranked_generic_hits,
            semantic_plan.get("concepts") or {},
            explicit_symbols=verified_explicit_symbols,
        )

        # 当前工作区问题一旦带有明确的“哪个版本开始/何时引入”语义，还需要一条
        # 独立的只读历史通道。它使用问题拆出的业务词检索所有已挂载子仓库的提交
        # 差异，再把提交涉及的类、配置和文件词带回当前源码验证；不把提交说明直接
        # 当成答案，也不依赖项目名或人工维护的版本映射。
        history_current_hits = []
        history_version_evidence = []
        if history_intent:
            history_terms = history_search_terms(
                current["question"],
                [
                    *generic_anchors,
                    *queries,
                    *(semantic_plan.get("code_terms") or []),
                ],
            )
            history_started_at = time.perf_counter()
            try:
                history_probe_hits = []
                for history_term in history_terms:
                    history_term_result = await asyncio.to_thread(
                        search_code_terms,
                        settings.code_repositories_root,
                        current["project"],
                        [history_term],
                        max_results=120,
                        extensions=[".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json"],
                        case_insensitive=True,
                    )
                    history_probe_hits.extend(
                        {
                            **item,
                            "matched_queries": [history_term],
                        }
                        for item in history_term_result.get("matches") or []
                    )
                history_terms = select_history_terms_from_current_hits(
                    history_terms,
                    history_probe_hits,
                )
                history_result = await asyncio.to_thread(
                    search_repository_history,
                    settings.code_repositories_root,
                    current["project"],
                    history_terms,
                )
                history_events = history_result.get("events") or []
                history_summary.update(
                    {
                        "terms": history_result.get("terms") or history_terms,
                        "repositories": history_result.get("repositories") or [],
                        "event_count": len(history_events),
                        "warnings": history_result.get("warnings") or [],
                        "elapsed_ms": round(
                            (time.perf_counter() - history_started_at) * 1000,
                            2,
                        ),
                    }
                )
                for history_warning in history_result.get("warnings") or []:
                    warning = f"Git 历史检索降级: {history_warning}"
                    if warning not in warnings:
                        warnings.append(warning)
                history_code_terms = list(
                    dict.fromkeys(str(item) for item in history_result.get("code_terms") or [])
                )[:20]
                if history_events and history_code_terms:
                    history_version_evidence = await asyncio.to_thread(
                        _history_version_parser_evidence,
                        settings.code_repositories_root,
                        current["project"],
                        history_code_terms,
                    )
                    current_result = await asyncio.to_thread(
                        search_code_terms,
                        settings.code_repositories_root,
                        current["project"],
                        history_code_terms,
                        max_results=MAX_GENERIC_HITS,
                        extensions=[".php", ".inc", ".phtml", ".conf", ".ini", ".yaml", ".yml", ".json"],
                        case_insensitive=True,
                    )
                    history_current_hits = annotate_hits_with_concepts(
                        [
                            {
                                **item,
                                "retrieval_stage": "history_related",
                            }
                            for item in current_result.get("matches") or []
                        ],
                        concept_match_terms,
                    )
            except (CodeToolError, OSError, ValueError) as exc:
                history_summary["warnings"] = [str(exc)]
                warnings.append(f"Git 历史检索降级: {exc}")
            retrieval_channel_summaries.append(
                {
                    "channel": "git_history",
                    "degraded": bool(history_summary.get("warnings")),
                    "hit_count": len(history_events),
                    "elapsed_ms": history_summary.get("elapsed_ms", 0.0),
                }
            )

        # 历史提交本身可以作为“何时引入”的证据来源，但提交涉及的当前源码仍要
        # 继续走符号索引和调用链，才能核对版本常量、配置开关和最终执行条件。
        retrieval_ready = bool(support["grounded"] or history_events)
        if retrieval_ready:
            generic_hits = select_semantic_hits(ranked_generic_hits, limit=36)
            # 首轮命中可能只是“发送短信失败”日志，而真正的配置键在相邻的
            # `Config::get()` 行。先从证据上下文提取真实配置读取，再交给索引
            # 发现补充符号；这样配置值不会因为日志行不含键名而被漏掉。
            config_definition_query_terms = config_definition_terms(
                _config_keys_from_hits(
                    _nearby_hit_contexts(
                        settings.code_repositories_root,
                        current["project"],
                        generic_hits,
                    )
                ),
                limit=8,
            )
        else:
            generic_hits = []

        if history_current_hits:
            generic_hits = rank_discovery_hits(
                [*generic_hits, *history_current_hits],
                limit=MAX_GENERIC_HITS,
                preferred_repository=preferred_repository,
            )

        if not retrieval_ready:
            # 没有可靠业务锚点或历史事件时继续做符号发现，会把整个仓库的通用词、
            # 测试和后台页面连成一条虚假调用链。这里明确停止扩展，只保留后续的
            # 原始查询审计和缺口说明，等待用户补充业务对象或显式符号。
            warning = (
                "未找到可相互印证的源码业务锚点，已停止概念扩展、符号发现和调用链补全；"
                "本次不会用泛化词拼凑业务结论。"
            )
            if warning not in warnings:
                warnings.append(warning)
            discovery_plan = {
                "queries": [],
                "sources": [],
                "mode": "insufficient_support",
            }

        if retrieval_ready:
            try:
                index_result = await load_project_symbol_index(
                    root=settings.code_repositories_root,
                    project=current["project"],
                    redis_url=settings.redis_url,
                    cache_enabled=settings.project_index_cache_enabled,
                    cache_ttl_seconds=settings.project_index_cache_ttl_seconds,
                    lock_ttl_seconds=settings.project_index_build_lock_seconds,
                    wait_timeout_seconds=settings.project_index_wait_timeout_seconds,
                )
                project_index = index_result.index
                index_prepare_summary = index_result.audit_dict()
                index_ready = True
                selected_config_keys = select_question_config_keys(
                    project_index,
                    semantic_plan.get("code_terms") or [],
                    # 金额问题通常同时涉及最低值、最高值、次数、渠道和换算比例。
                    # 配置索引的顺序会随大仓库扫描顺序变化，只取少量键可能丢掉
                    # 比例定义；八个仍受后续总证据预算约束，不会无限扩张。
                    limit=8,
                )
                discovery_plan = discover_related_queries(
                    project_index,
                    generic_hits,
                    existing_queries=queries,
                    limit=24,
                    preferred_repository=preferred_repository,
                )
                discovery_plan["mode"] = "semantic_discovery"
                discovered_query_set = set(discovery_plan["queries"])
                config_definition_query_terms = list(
                    dict.fromkeys(
                        [
                            *config_definition_query_terms,
                            *config_definition_terms(selected_config_keys, limit=6),
                            *config_definition_terms(
                                str(item.get("key") or "")
                                for item in project_index.get("configs") or []
                                if str(item.get("key") or "") in discovered_query_set
                            ),
                        ]
                    )
                )
                environment_queries = []
                if re.search(
                    r"(?:测试|正式|生产)(?:环境)?|\b(?:test|production)\s+environment\b",
                    current["question"],
                    re.IGNORECASE,
                ):
                    environment_queries.append("getByTestMode")
                # 完整配置键负责证明“代码读取了该配置”，末级字段名负责回到配置
                # 定义处读取真实值；环境覆盖问题再补查共性环境选择函数的实现。
                # 三者都来自问题概念与项目索引的交集，不包含完整问题到答案的映射。
                queries = list(
                    dict.fromkeys(
                        [
                            *selected_config_keys,
                            *queries,
                            *config_definition_query_terms,
                            *discovery_plan["queries"],
                            *environment_queries,
                        ]
                    )
                )[:40]
            except (CodeToolError, OSError, ValueError) as exc:
                warnings.append(f"符号索引发现降级: {exc}")
        evidence.extend(
            {
                "type": "generic_code",
                "project": current["project"],
                "query": "通用业务片段",
                "generic_rank": rank,
                "matched_queries": item.get("matched_queries") or [],
                **{key: value for key, value in item.items() if key != "matched_queries"},
            }
            for rank, item in enumerate(generic_hits)
        )
        # 概念英文词如果正好是配置文件名词干（如 noble 概念 → newNobleLv.php），
        # 直接把该配置加入证据。全文检索会因同词文件过多在截断处丢掉定义文件，
        # 文件名词干匹配可以稳定找回“配置定义处”，仍由后续源码分析验证取值。
        evidence.extend(
            {"project": current["project"], **item}
            for item in _concept_config_file_evidence(
                settings.code_repositories_root,
                current["project"],
                semantic_terms,
                limit=3,
            )
        )
        evidence.extend(history_version_evidence)
        # 完整点号键可能来自首轮调用点提取，负责证明业务读取了配置，但不含真实
        # 数值。检索前从全部规划键重新提取末级字段，确保 `dollar_to_diamond`
        # 这类比例键即使未进入前八个配置候选，也能定向回到定义行。
        config_definition_query_terms = list(
            dict.fromkeys(
                [
                    *config_definition_query_terms,
                    *config_definition_terms(queries, limit=12),
                ]
            )
        )
        term_search_started_at = time.perf_counter()
        try:
            query_evidence = _search_query_evidence(
                settings.code_repositories_root,
                current["project"],
                queries,
                config_definition_query_terms,
            )
            evidence.extend(query_evidence)
            term_search_summary.update(
                {
                    "query_count": len(list(dict.fromkeys(queries))),
                    "match_count": len(query_evidence),
                }
            )
        except CodeToolError as exc:
            warnings.append(str(exc))
        finally:
            term_search_summary["elapsed_ms"] = round(
                (time.perf_counter() - term_search_started_at) * 1000,
                2,
            )
        evidence.extend(
            {"project": current["project"], **item}
            for item in _symbol_evidence(project_index, queries)
        )
        call_chains = []
        if index_ready and retrieval_ready:
            try:
                call_chain_entries = select_call_chain_entries(
                    project_index,
                    queries,
                    evidence,
                    limit=8,
                )
                call_chains = [
                    trace_index_call_chain(project_index, entry, depth=5)
                    for entry in call_chain_entries
                ]
                evidence.extend(
                    {"project": current["project"], **item}
                    for item in _call_chain_evidence(call_chains)
                )
            except (CodeToolError, ValueError) as exc:
                warnings.append(f"调用链证据补全降级: {exc}")
        evidence = annotate_hits_with_concepts(evidence, concept_match_terms)
        deduplicated = _deduplicate_evidence(evidence)
        explicit_target_files = _select_explicit_target_files(
            project_index,
            deduplicated,
            explicit_symbols,
            current["question"],
        )
        deduplicated = _focus_explicit_target_evidence(
            deduplicated,
            explicit_target_files,
            explicit_symbols,
        )
        try:
            inherited_evidence = _inherited_class_evidence(
                settings.code_repositories_root,
                current["project"],
                project_index,
                explicit_target_files,
            )
            deduplicated.extend(inherited_evidence)
            inherited_target_files = [
                str(item.get("file") or "")
                for item in inherited_evidence
                if item.get("file")
            ]
        except (CodeToolError, ValueError) as exc:
            warnings.append(f"继承层级证据补全降级: {exc}")
        deduplicated.sort(key=_evidence_relevance)
        if retrieval_ready:
            deduplicated = _select_diagnostic_evidence(
                deduplicated,
                queries,
                MAX_DIAGNOSTIC_EVIDENCE,
                explicit_target_files,
            )
        else:
            deduplicated = []
        for item in deduplicated:
            try:
                start_line, end_line = _method_evidence_range(project_index, item)
                # 枚举类问题（每个等级/所有档位）需要完整配置文件窗口，按方法
                # 命中范围截取会只看到前几个等级，导致答案不完整。
                evidence_file = str(item.get("file") or "")
                if (
                    (
                        evidence_file.endswith("config.php")
                        or "/config/" in f"/{evidence_file}"
                    )
                    and any(
                        token in current["question"]
                        for token in ("每个", "所有", "全部", "列出", "分别", "各等级")
                    )
                ):
                    # read_file 单次最多读取 1000 行，这里取允许的最大窗口。
                    start_line, end_line = 1, 1001
                context = read_file(
                    settings.code_repositories_root,
                    current["project"],
                    item["file"],
                    start_line,
                    end_line,
                )
                item["line_start"] = context["start_line"]
                item["line_end"] = context["end_line"]
                item["context"] = context["content"]
                await insert_evidence(pool, task_id, {
                    **item,
                    "content": context["content"],
                    "end_line": context["end_line"],
                })
            except CodeToolError as exc:
                warnings.append(str(exc))
        symbol_summary = {"symbols": [], "references": [], "configs": [], "sql": []}
        if support["grounded"]:
            try:
                if not index_ready:
                    raise CodeToolError("PHP 符号索引未成功建立")
                symbol_sync_summary = await replace_symbol_index_if_changed(
                    pool,
                    current["project"],
                    index_prepare_summary["fingerprint"],
                    project_index,
                )
                symbol_summary = summarize_symbol_index(
                    project_index,
                    current["project"],
                    limit=80,
                    queries=queries,
                    evidence=deduplicated,
                )
            except (CodeToolError, ValueError) as exc:
                warnings.append(str(exc))
        await update_task(
            pool,
            task_id,
            status="VERIFYING",
            progress=65,
            current_step="正在执行按需只读验证",
        )
        await record_trace_span(
            pool,
            task_id,
            "stage.completed",
            "retrieval",
            "succeeded" if retrieval_ready else "insufficient_support",
            elapsed_ms=(time.perf_counter() - retrieval_started_at) * 1000,
            payload={
                "evidence_count": len(deduplicated),
                "symbol_count": len(symbol_summary.get("symbols") or []),
                "call_chain_count": len(call_chains),
                "retrieval_mode": discovery_plan.get("mode") or "semantic_discovery",
                "degraded": bool(warnings),
                "warning_count": len(warnings),
            },
        )
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        await record_trace_span(
            pool,
            task_id,
            "stage.started",
            "validation",
            "started",
            payload={},
        )
        validation_started_at = time.perf_counter()
        validation = await execute_validation_plan(
            settings,
            pool,
            task_id,
            current.get("validation_plan"),
        )
        await record_trace_span(
            pool,
            task_id,
            "stage.completed",
            "validation",
            "succeeded",
            elapsed_ms=(time.perf_counter() - validation_started_at) * 1000,
            payload={
                "database_status": (validation.get("database") or {}).get("status", "not_requested"),
                "config_count": len(validation.get("configs") or []),
                "log_count": len(validation.get("logs") or []),
            },
        )
        await update_task(
            pool,
            task_id,
            status="REPORTING",
            progress=80,
            current_step="正在生成代码检索报告",
        )
        await record_trace_span(
            pool,
            task_id,
            "stage.started",
            "analysis",
            "started",
            payload={},
        )
        analysis_started_at = time.perf_counter()
        current = await get_task(pool, task_id)
        if current is None or current["status"] == "CANCELLED":
            return
        report_result = {
            "message": "已完成相关问题代码检索、证据过滤和确定性业务分析。",
            "queries": queries,
            "retrieval_plan": {
                "generic_anchor_count": len(generic_anchors),
                "initial_generic_hit_count": len(initial_generic_hits),
                "semantic_vector_hit_count": len(semantic_vector_hits),
                "semantic_vector": semantic_vector_summary,
                "semantic_hit_count": len(semantic_hits),
                "focused_hit_count": len(generic_hits),
                "preferred_repository": preferred_repository,
                "semantic_concepts": semantic_plan.get("concepts") or {},
                "semantic_code_terms": semantic_plan.get("code_terms") or [],
                "explicit_symbols": explicit_symbols,
                "verified_explicit_symbols": verified_explicit_symbols,
                "explicit_target_files": explicit_target_files,
                "inherited_target_files": list(dict.fromkeys(inherited_target_files)),
                "config_definition_queries": config_definition_query_terms,
                "discovered_queries": discovery_plan.get("queries") or [],
                "discovery_sources": discovery_plan.get("sources") or [],
                "retrieval_mode": discovery_plan.get("mode") or "semantic_discovery",
                "retrieval_support": support,
                "history": history_summary,
                "project_index": index_prepare_summary,
                "term_search": term_search_summary,
                "retrieval_channels": retrieval_channel_summaries,
                "symbol_persistence": symbol_sync_summary,
            },
            "evidence": deduplicated,
            "symbols": symbol_summary["symbols"],
            "references": symbol_summary["references"],
            "configs": symbol_summary["configs"],
            "sql": symbol_summary["sql"],
            "call_chains": call_chains,
            "validation": validation,
            "history_events": history_events,
            "warnings": warnings,
        }
        report_result["deterministic_findings"] = build_deterministic_findings(
            current["question"], report_result
        )
        deterministic_findings = report_result["deterministic_findings"]
        await record_trace_span(
            pool,
            task_id,
            "stage.completed",
            "deterministic_analysis",
            "succeeded",
            elapsed_ms=(time.perf_counter() - analysis_started_at) * 1000,
            payload={
                "fact_count": len(deterministic_findings.get("facts") or []),
                "evidence_ref_count": len(deterministic_findings.get("evidence_refs") or []),
            },
        )
        available_evidence = count_available_evidence(report_result)
        if should_run_ai(settings, current, available_evidence):
            await record_trace_span(
                pool,
                task_id,
                "stage.started",
                "ai_narration",
                "started",
                payload={"evidence_count": available_evidence},
            )
            ai_started_at = time.perf_counter()
            try:
                ai_run = await generate_ai_diagnosis_cached(
                    settings,
                    current["project"],
                    current["question"],
                    report_result,
                )
                report_result["ai_diagnosis"] = ai_run.diagnosis.model_dump()
                report_result["ai_metadata"] = ai_run.metadata
                await record_trace_span(
                    pool,
                    task_id,
                    "stage.completed",
                    "ai_narration",
                    "degraded" if ai_run.metadata.get("degraded") else "succeeded",
                    elapsed_ms=(time.perf_counter() - ai_started_at) * 1000,
                    payload={
                        "model": ai_run.metadata.get("model", ""),
                        "protocol": ai_run.metadata.get("protocol", ""),
                        "attempt_count": ai_run.metadata.get("attempt_count", 1),
                        "fallback_reason": ai_run.metadata.get("fallback_reason", ""),
                        "evidence_count": ai_run.metadata.get("evidence_count", 0),
                    },
                )
            except Exception as exc:
                error_metadata = getattr(exc, "metadata", None)
                if isinstance(error_metadata, dict):
                    report_result["ai_metadata"] = dict(error_metadata)
                logger.warning(
                    "ai_diagnosis_failed task_id=%s error_type=%s error_message=%s",
                    task_id,
                    type(exc).__name__,
                    str(exc)[:300],
                )
                warnings.append("AI 诊断叙写不可用，已保留确定性业务分析与代码证据")
                await record_trace_span(
                    pool,
                    task_id,
                    "stage.failed",
                    "ai_narration",
                    "degraded",
                    elapsed_ms=(time.perf_counter() - ai_started_at) * 1000,
                    payload={
                        "error_type": (
                            error_metadata.get("error_type")
                            if isinstance(error_metadata, dict)
                            else type(exc).__name__
                        ),
                        "error_message": str(exc)[:300],
                    },
                )
        else:
            skip_warning = ai_skip_warning(settings, current, available_evidence)
            if skip_warning:
                warnings.append(skip_warning)
            await record_trace_span(
                pool,
                task_id,
                "stage.skipped",
                "ai_narration",
                "skipped",
                payload={"reason": skip_warning or "llm_disabled"},
            )
        report_result["assessment"] = build_assessment(report_result)
        reporting_started_at = time.perf_counter()
        await update_task(
            pool,
            task_id,
            status="COMPLETED",
            progress=100,
            current_step="诊断报告已生成",
            result=report_result,
        )
        await record_trace_span(
            pool,
            task_id,
            "task.completed",
            "reporting",
            "succeeded",
            elapsed_ms=(time.perf_counter() - reporting_started_at) * 1000,
            payload={
                "confidence": (report_result.get("assessment") or {}).get("confidence", {}).get("label", ""),
                "warning_count": len(warnings),
            },
        )
    except SoftTimeLimitExceeded as exc:
        await update_task(
            pool,
            task_id,
            status="FAILED",
            progress=100,
            current_step="任务执行超时",
            error="任务超过软超时限制，已停止执行",
        )
        await record_trace_span(
            pool,
            task_id,
            "task.failed",
            "timeout",
            "failed",
            elapsed_ms=(time.perf_counter() - diagnosis_started_at) * 1000,
            payload={"error_type": type(exc).__name__},
        )
        raise
    except Exception as exc:
        await update_task(
            pool,
            task_id,
            status="FAILED",
            progress=100,
            current_step="任务执行失败",
            error=str(exc),
        )
        await record_trace_span(
            pool,
            task_id,
            "task.failed",
            "worker",
            "failed",
            elapsed_ms=(time.perf_counter() - diagnosis_started_at) * 1000,
            payload={"error_type": type(exc).__name__},
        )
        raise
    finally:
        await pool.close()
