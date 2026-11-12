"""显式类、动作覆盖和通用控制点事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.code_tools import extract_generic_anchors
from app.query_understanding import detect_semantic_concepts
from app.domain.diagnosis.analyzers.common import EvidenceLine, _find_line


_EXPLICIT_CLASS_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9_]{2,})(?:::[A-Za-z_][A-Za-z0-9_]*)?\b"
)

_METHOD_DECLARATION_PATTERN = re.compile(
    r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)

_REQUESTED_ACTION_PROBES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "累计",
        ("累计", "累加", "增加"),
        ("hincrby(", "incrby(", "incr(", "addscore(", "+="),
    ),
    (
        "消耗",
        ("消耗", "扣减", "扣除", "减少", "花费"),
        ("hdecrby(", "decrby(", "decr(", "subscore(", "deduct", "consume", "spend", "-="),
    ),
    (
        "清理",
        ("清理", "删除"),
        ("->expire(", "->del(", "->unlink(", "delete(", "remove("),
    ),
    (
        "写入",
        ("写入", "记录", "创建"),
        ("hset(", "hmset(", "setex(", "setnx(", "create(", "createrecord(", "insert"),
    ),
    (
        "更新",
        ("更新", "修改"),
        ("hupdate(", "update(", "update_by", "save("),
    ),
)

def _explicit_class_names(question: str) -> List[str]:
    """提取问题中可由源码文件验证的显式 PHP 类名。

    参数:
        - `question`：用户提交的业务诊断问题，类型为 `str`。

    Returns:
        保持出现顺序的类名列表。HTTP、SQL、Redis 等技术缩写不会作为目标业务类；
        若用户给出 `Class::method`，这里只保留 `Class` 用于定位类文件，方法名由
        后续源码行继续约束。
    """
    ignored = {
        "API", "HTTP", "HTTPS", "ID", "JSON", "PHP", "REDIS", "SQL", "URL",
        "UUID", "YAML",
    }
    names = []
    for match in _EXPLICIT_CLASS_PATTERN.finditer(question):
        name = match.group(1)
        if name.upper() in ignored or name.isupper():
            continue
        if name not in names:
            names.append(name)
    return names

def _explicit_symbol_line_signal(content: str) -> Tuple[str, int]:
    """为显式目标类中的源码行计算与业务问题相关的通用信号。

    参数:
        - `content`：目标类文件中的一行源码，类型为 `str`。

    Returns:
        `(事实类别, 权重)`。权重只用于在有限证据预算内优先保留配置、条件、
        队列、奖励、余额变更和返回结构；它不根据项目名或具体类名判断答案。
    """
    stripped = content.strip()
    lowered = stripped.casefold()
    if not stripped or stripped.startswith(("*", "//", "/*", "#", "use ", "namespace ")):
        return "", 0
    if re.match(
        r"^(?:public|protected|private)?\s*static\s+(?:int|float)\s+\$[a-z_][a-z0-9_]*\s*=\s*-?\d+(?:\.\d+)?\s*;",
        lowered,
    ):
        return "数值边界", 44
    if "getconfig(" in lowered or "config::get" in lowered:
        return "配置来源", 48
    if lowered.startswith(("const ", "private const ", "protected const ", "public const ")) and any(
        token in lowered for token in ("lock", "expire", "timeout")
    ):
        return "锁与幂等", 44
    if "runningconfigs" in lowered or "configservice" in lowered:
        return "活动配置", 44
    if lowered.startswith(("if ", "if(", "elseif", "} elseif")) and any(
        token in lowered for token in ("etime", "stime", "strtotime", "formatdate", "time()")
    ):
        return "时间边界", 44
    if lowered.startswith(("if ", "if(", "elseif", "} elseif", "switch", "case ")):
        return "业务条件", 42
    if any(token in lowered for token in ("formatdate", "strtotime", "time()", "gettm(")):
        return "时间边界", 32
    if any(token in lowered for token in ("lpush(", "rpush(", "brpop(", "blpop(", "addprizequeue(")):
        return "队列流转", 46
    if "scoreservice->" in lowered or any(
        token in lowered for token in ("getgiftscore(", "calculatescore(", "getscore(")
    ):
        return "积分计算", 44
    if "levelrewardservice->" in lowered:
        return "档位奖励", 44
    if "->retry(" in lowered:
        return "失败重试", 40
    if any(token in lowered for token in ("sendreward(", "grant(", "dispatcher->", "createRecord", "sendmsg(")):
        return "奖励派发", 44
    if any(token in lowered for token in ("->expire(", "->del(", "->unlink(", "delete(", "remove(")):
        return "清理动作", 42
    if any(token in lowered for token in ("hincrby(", "addscore(", "finishtask(", "setnx(", "hsetnx(")):
        return "计数与状态变更", 42
    if "rediskey(" in lowered or "getrediskey(" in lowered or "redis_key" in lowered:
        return "Redis Key", 38
    if any(token in lowered for token in ("zrevrange(", "zrange(", "brpop(", "blpop(", "hscan(")) or re.search(
        r"(?:->|::)get\s*\(|\bexists\s*\(",
        lowered,
    ):
        return "数据读取", 34
    if lowered.startswith("return") and re.search(
        r"\$[a-z_][a-z0-9_]*\s*[+\-*/%]\s*|"
        r"[+\-*/%]\s*\$[a-z_][a-z0-9_]*|\d\s*[+\-*/%]\s*\d",
        lowered,
    ):
        return "计算规则", 38
    if lowered.startswith("return") and ("[" in stripped or "->" in stripped or "(" in stripped):
        return "返回结构", 30
    if "foreach (" in lowered or lowered.startswith("foreach"):
        return "遍历范围", 26
    return "", 0

def _explicit_target_source_lines(
    lines: Sequence[EvidenceLine],
    question: str,
) -> List[EvidenceLine]:
    """筛出问题显式指向的类文件源码行，并处理不同模块的同名类。

    参数:
        - `lines`：本次证据窗口展开后的全部源码行，类型为
          `Sequence[EvidenceLine]`。
        - `question`：用户原始问题，类型为 `str`；其中真正的 PHP 类名用于确定
          文件主干，其他英文模块词用于同名类消歧。

    Returns:
        目标类文件的源码行。问题没有显式类名或证据未包含目标类时返回空列表；
        存在多个同名类且问题给出了路径模块词时，只返回路径匹配的候选。
    """
    class_names = _explicit_class_names(question)
    if not class_names:
        return []
    class_set = set(class_names)
    all_target_lines = []
    for item in lines:
        basename = item.file.replace("\\", "/").rsplit("/", 1)[-1]
        class_stem = basename.rsplit(".", 1)[0]
        if class_stem in class_set:
            all_target_lines.append(item)

    common_scope_tokens = {
        "API", "HTTP", "HTTPS", "ID", "JSON", "PHP", "Redis", "SQL", "URL",
        "UUID", "YAML",
    }
    scope_tokens = [
        value
        for value in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", question)
        if value not in class_set and value not in common_scope_tokens
    ]
    scoped_lines = [
        item
        for item in all_target_lines
        if any(token.casefold() in item.file.casefold() for token in scope_tokens)
    ]
    return scoped_lines or all_target_lines

def _requested_action_gaps(
    lines: Sequence[EvidenceLine],
    question: str,
    inherited_files: Sequence[str] = (),
) -> List[str]:
    """识别问题询问的数据流动作是否出现在显式目标类源码中。

    参数:
        - `lines`：本次证据窗口展开后的全部源码行，类型为
          `Sequence[EvidenceLine]`。
        - `question`：用户原始问题，类型为 `str`；仅通用的累计、消耗、清理、
          写入和更新动作会触发覆盖检查。
        - `inherited_files`：Worker 已确认的直接和间接基类文件，类型为
          `Sequence[str]`；继承实现也属于目标类可执行行为的一部分。

    Returns:
        目标类源码未出现对应代码信号的证据边界说明。缺失只表示当前目标类证据
        窗口未观察到该动作，不能扩大解释为整个项目不存在相关能力。
    """
    target_lines = _explicit_target_source_lines(lines, question)
    inherited_set = set(inherited_files)
    target_lines.extend(item for item in lines if item.file in inherited_set)
    if not target_lines:
        return []
    source = "\n".join(item.content.casefold() for item in target_lines)
    gaps = []
    for action, triggers, code_signals in _REQUESTED_ACTION_PROBES:
        if not any(trigger in question for trigger in triggers):
            continue
        if any(signal in source for signal in code_signals):
            continue
        gaps.append(
            f"问题同时询问“{action}”相关行为，但当前目标类证据窗口未观察到对应的"
            "数据变更语句；这不等于整个项目不存在该能力，仍需继续核对调用方、父类或相邻服务。"
        )
        if len(gaps) >= 3:
            break
    return gaps

def _source_covers_action(
    lines: Sequence[EvidenceLine],
    code_signals: Sequence[str],
) -> bool:
    """判断一段源码证据是否包含指定业务动作的真实代码写法。

    参数:
        - `lines`：待检查的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `code_signals`：`_REQUESTED_ACTION_PROBES` 中与动作对应的代码片段，
          类型为 `Sequence[str]`；用于确认累计、消耗、清理等行为是否真的出现在
          当前证据窗口。

    Returns:
        至少命中一个代码片段时返回 `True`。匹配只说明源码中存在该写法，不说明
        它一定属于用户询问的业务分支，调用方仍需结合目标类和问题语义筛选。
    """
    source = "\n".join(item.content.casefold() for item in lines)
    return any(signal.casefold() in source for signal in code_signals)

def _inherited_source_matches_question(
    lines: Sequence[EvidenceLine],
    question: str,
) -> bool:
    """判断父类证据是否与用户问题存在可解释的业务关联。

    参数:
        - `lines`：某个已验证直接基类的全部证据行，类型为
          `Sequence[EvidenceLine]`。
        - `question`：用户原始业务问题，类型为 `str`；用于提取四字以上业务
          锚点和可组合语义概念。

    Returns:
        父类源码包含问题业务锚点、语义概念对应代码词或中文触发词时返回 `True`。
        该方法只决定父类是否值得进入事实分析，不把父类出现本身当成业务结论。
    """
    source = "\n".join(item.content for item in lines).casefold()
    anchors = [
        anchor
        for anchor in extract_generic_anchors(question)
        if len(anchor) >= 4
    ]
    if any(anchor.casefold() in source for anchor in anchors):
        return True
    for concept in detect_semantic_concepts(question):
        if any(term.casefold() in source for term in concept.code_terms):
            return True
        if any(trigger.casefold() in source for trigger in concept.triggers):
            return True
    return False

def _relevant_inherited_source_lines(
    target_lines: Sequence[EvidenceLine],
    all_lines: Sequence[EvidenceLine],
    question: str,
    inherited_files: Sequence[str],
) -> List[EvidenceLine]:
    """选择能够补充目标类事实缺口的直接基类源码。

    参数:
        - `target_lines`：用户显式指定类的源码证据行，类型为
          `Sequence[EvidenceLine]`。
        - `all_lines`：本次证据窗口展开后的全部源码行，类型为
          `Sequence[EvidenceLine]`；其中包含 Worker 已确认的直接基类。
        - `question`：用户原始业务问题，类型为 `str`；用于判断询问了哪些数据流
          动作以及父类是否与业务对象相关。
        - `inherited_files`：Worker 沿真实 `extends` 声明验证过的基类文件，
          类型为 `Sequence[str]`。

    Returns:
        可纳入事实分析的基类源码行。目标类已经提供充足证据且没有缺失动作时，
        不为通用父类额外制造事实；目标类缺少“消耗”等明确动作时，只有真正包含
        该动作的父类才会入选。这样既保留模板方法实现，又避免 `getType`、
        `__destruct`、`getInstance` 等通用基础设施方法污染业务结论。
    """
    inherited_set = set(inherited_files)
    if not inherited_set:
        return []
    target_signal_count = sum(
        1
        for item in target_lines
        if _explicit_symbol_line_signal(item.content)[1] > 0
    )
    requested_actions = [
        probe
        for probe in _REQUESTED_ACTION_PROBES
        if any(trigger in question for trigger in probe[1])
    ]
    missing_actions = [
        probe
        for probe in requested_actions
        if not _source_covers_action(target_lines, probe[2])
    ]
    if target_signal_count >= 4 and not missing_actions:
        return []

    selected: List[EvidenceLine] = []
    for file_name in dict.fromkeys(inherited_files):
        parent_lines = [item for item in all_lines if item.file == file_name]
        if not parent_lines:
            continue
        if missing_actions:
            covers_missing_action = any(
                _source_covers_action(parent_lines, probe[2])
                for probe in missing_actions
            )
            if not covers_missing_action:
                continue
        elif not _inherited_source_matches_question(parent_lines, question):
            continue
        selected.extend(parent_lines)
    return selected

def _explicit_question_category_bonus(question: str, category: str) -> int:
    """按用户询问动作提高同类源码行的优先级。

    参数:
        - `question`：用户原始业务问题，类型为 `str`。
        - `category`：`_explicit_symbol_line_signal` 返回的通用源码类别，类型为
          `str`。

    Returns:
        与问题动作匹配时增加的排序权重。这里只理解“队列、奖励、条件、Key、
        累计、时间”等可复用业务动作，不包含项目名、类名或具体答案。
    """
    bonus = 0
    if "排队" in question or "队列" in question:
        bonus += 100 if category == "队列流转" else 0
    if "发放" in question or "奖励" in question or "派发" in question:
        bonus += 90 if category in {"奖励派发", "档位奖励"} else 0
    if "清理" in question and ("key" in question.casefold() or "Key" in question):
        bonus += 100 if category in {"Redis Key", "清理动作"} else 0
    if any(value in question for value in ("累计", "消耗", "积分", "余额")):
        bonus += 80 if category in {"计数与状态变更", "积分计算"} else 0
    if any(value in question for value in ("条件", "按什么", "满足哪些", "判断")):
        bonus += 70 if category in {"业务条件", "锁与幂等"} else 0
    if "配置" in question:
        bonus += 80 if category in {"配置来源", "活动配置"} else 0
    if any(value in question for value in ("时间", "周期", "频次", "触发边界")):
        bonus += 60 if category in {"时间边界", "活动配置"} else 0
    if any(value in question for value in ("范围", "边界", "保留", "周期")):
        bonus += 60 if category in {"时间边界", "数值边界", "计算规则"} else 0
    return bonus

def _explicit_symbol_facts(
    lines: Sequence[EvidenceLine],
    question: str,
    limit: int = 12,
    inherited_files: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """从用户显式指定类的源码中提取可审计的条件和数据来源。

    参数:
        - `lines`：本次已检索并展开的源码行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户原始问题，用于识别显式类名，类型为 `str`。
        - `limit`：最多返回的源码事实数量，类型为 `int`。
        - `inherited_files`：Worker 从 `extends` 声明确认的基类文件，类型为
          `Sequence[str]`；只纳入这些已验证基类，不扫描其他同名类。

    Returns:
        只引用目标类文件的事实列表。该方法不猜测完整业务答案，而是按通用代码
        结构提取配置读取、条件、队列、奖励、计数、Key、时间边界和返回结构，
        让人工能够从真实源码继续判断业务规则。没有目标类文件证据时返回空列表。
    """
    if limit < 1 or limit > 24:
        raise ValueError("显式符号事实数量必须在 1 到 24 之间")
    # 不同模块经常存在同名服务。问题给出目录或模块词时，沿用同一套路径消歧
    # 规则，确保事实提取与 Worker 选中的目标文件完全一致。
    target_lines = _explicit_target_source_lines(lines, question)
    if not target_lines:
        return []
    inherited_set = set(inherited_files)
    target_lines.extend(
        _relevant_inherited_source_lines(
            target_lines,
            lines,
            question,
            inherited_files,
        )
    )

    candidates: List[Tuple[int, int, EvidenceLine, str, str]] = []
    current_method = "类级逻辑"
    for order, item in enumerate(target_lines):
        declaration = _METHOD_DECLARATION_PATTERN.search(item.content)
        if declaration:
            current_method = declaration.group(1)
            continue
        category, score = _explicit_symbol_line_signal(item.content)
        if score <= 0:
            continue
        score += _explicit_question_category_bonus(question, category)
        candidates.append((score, order, item, category, current_method))
    candidates.sort(key=lambda entry: (-entry[0], entry[1]))

    facts = []
    per_method: Dict[str, int] = {}
    per_method_category: Dict[Tuple[str, str], int] = {}
    global_category: Dict[str, int] = {}
    category_soft_cap = max(2, min(4, limit // 3))
    deferred_candidates = []
    seen_refs = set()
    for _, _, item, category, method_name in candidates:
        category_key = (method_name, category)
        if (
            item.ref in seen_refs
            or per_method.get(method_name, 0) >= 5
            or per_method_category.get(category_key, 0) >= 3
        ):
            continue
        if global_category.get(category, 0) >= category_soft_cap:
            # 同一个目标类常包含大量结构相似的条件判断。若按全局分数直接截断，
            # 一类高频条件会挤掉问题同时询问的时间边界、配置和奖励动作。第一轮
            # 先限制单类占比，第二轮再补足不足，兼顾问题覆盖和事实总量。
            deferred_candidates.append((item, category, method_name))
            continue
        seen_refs.add(item.ref)
        per_method[method_name] = per_method.get(method_name, 0) + 1
        per_method_category[category_key] = per_method_category.get(category_key, 0) + 1
        global_category[category] = global_category.get(category, 0) + 1
        source_subject = method_name
        if item.file in inherited_set:
            source_class = item.file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            source_subject = f"父类 {source_class} 的 {method_name}"
        facts.append(
            {
                "category": category,
                "statement": f"{source_subject} 中的{category}源码：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )
        if len(facts) >= limit:
            return facts
    for item, category, method_name in deferred_candidates:
        category_key = (method_name, category)
        if (
            item.ref in seen_refs
            or per_method.get(method_name, 0) >= 5
            or per_method_category.get(category_key, 0) >= 3
        ):
            continue
        seen_refs.add(item.ref)
        per_method[method_name] = per_method.get(method_name, 0) + 1
        per_method_category[category_key] = per_method_category.get(category_key, 0) + 1
        source_subject = method_name
        if item.file in inherited_set:
            source_class = item.file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            source_subject = f"父类 {source_class} 的 {method_name}"
        facts.append(
            {
                "category": category,
                "statement": f"{source_subject} 中的{category}源码：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )
        if len(facts) >= limit:
            break
    return facts

def _generic_facts(lines: Sequence[EvidenceLine], question: str) -> List[Dict[str, Any]]:
    """为没有专用解释器的问题提取与问题语义共现的业务控制点。

    参数:
        - `lines`：当前问题命中的源码证据行，类型为 `Sequence[EvidenceLine]`。
        - `question`：用户提交的原始问题，类型为 `str`；用于要求控制点必须与
          业务对象或足够的概念共同出现，不能只凭 `return false` 等通用结构入选。

    Returns:
        最多 8 条包含拒绝、提前返回或错误码的控制点。每行至少满足以下条件
        之一才会进入结果：直接包含问题中的四字以上中文业务锚点，或同时覆盖
        两个问题概念且包含至少一个领域概念。该兜底只复述源码行，不把通用代码
        形态擅自解释成根因或完整业务规则。
    """
    signals = ("application::stop", "throw new", "returnfail(", "return false", "return null")
    question_concepts = {
        concept.name for concept in detect_semantic_concepts(question)
    }
    generic_concepts = {"amount", "config", "daily", "display", "gift", "limit", "status", "user"}
    domain_concepts = question_concepts - generic_concepts
    anchors = [value for value in extract_generic_anchors(question) if len(value) >= 4]
    required_coverage = min(2, len(question_concepts))
    facts = []
    seen_refs = set()
    for item in lines:
        if not any(signal in item.content.casefold() for signal in signals):
            continue
        folded_content = item.content.casefold()
        has_direct_anchor = any(anchor.casefold() in folded_content for anchor in anchors)
        line_concepts = {
            concept.name for concept in detect_semantic_concepts(item.content)
        }
        covered = question_concepts.intersection(line_concepts)
        if not has_direct_anchor:
            # 只有通用概念（配置、用户、状态）时不能把任意 `throw new`、`return
            # false` 当成业务答案。支付配置和开关类问题常出现这种组合，若放行会
            # 用无关异常分支冒充结论；此时应留空并让上层报告证据缺口。
            if not domain_concepts:
                continue
            if len(covered) < required_coverage:
                continue
            if not covered.intersection(domain_concepts):
                continue
        if item.ref in seen_refs:
            continue
        seen_refs.add(item.ref)
        facts.append(
            {
                "category": "问题相关控制点",
                "statement": f"问题相关源码中的控制点：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )
        if len(facts) >= 8:
            break
    return facts

def _representative_fact_snippets(category: str, snippets: Sequence[str]) -> List[str]:
    """为一类源码事实选择覆盖不同动作的代表片段。

    参数:
        - `category`：事实类别，例如“队列流转”或“业务条件”。
        - `snippets`：同类别按相关度排序的源码片段。

    Returns:
        最多四条代表片段。队列优先分别覆盖入队、阻塞出队和奖励入分发队列；
        条件优先覆盖共享判断、记录排除和空集合边界；其他类别按原始顺序取前两条。
        这些只是通用代码动作优先级，不绑定任何项目或类名。
    """
    if category == "队列流转":
        priorities = ("lpush(", "rpush(", "brpop(", "blpop(", "addprizequeue(")
    elif category == "业务条件":
        priorities = ("otheroperationkeys", "currentrecordid", "shared", "empty(")
    elif category in {"时间边界", "锁与幂等"}:
        priorities = ("lock", "expire", "etime", "stime", "formatdate")
    elif category == "奖励派发":
        priorities = ("sendreward(", "grant(", "addprizequeue(")
    elif category == "清理动作":
        priorities = ("expire(", "del(", "unlink(", "remove(")
    elif category in {"计算规则", "数值边界"}:
        priorities = ("reserve", "expire", "limit", "min", "max", "current", "etime", "stime")
    elif category == "积分计算":
        priorities = ("realscore(", "scorekey(", "periodkey(", "getgiftscore(")
    else:
        return list(snippets[:2])

    selected = []
    for priority in priorities:
        candidate = next(
            (
                snippet
                for snippet in snippets
                if priority in snippet.casefold() and snippet not in selected
            ),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
        if len(selected) >= 4:
            break
    for snippet in snippets:
        if snippet not in selected:
            selected.append(snippet)
        if len(selected) >= 2:
            break
    return selected

def _explicit_fact_conclusion(
    facts: Sequence[Dict[str, Any]],
    class_names: Sequence[str],
    inherited_files: Sequence[str] = (),
) -> str:
    """把显式目标类的事实按可复用业务类别压缩成可读结论。

    参数:
        - `facts`：`_explicit_symbol_facts` 从目标类源码提取的控制点，顺序已经按
          与问题动作的相关度排序。
        - `class_names`：用户问题中显式指定的类或模块标识，用于说明结论边界。
        - `inherited_files`：已纳入事实的直接基类文件，类型为
          `Sequence[str]`；存在时结论明确说明还包含继承实现。

    返回:
        一段不依赖具体项目答案的类内机制摘要。每个类别取最多两条真实源码片段，
        只复述配置、条件、队列、积分、奖励、锁和 Key 等结构，不推断证据之外的
        业务数值，也不把同义检索命中当作确定规则。
    """
    category_labels = {
        "配置来源": "配置来源",
        "活动配置": "活动配置依赖",
        "锁与幂等": "并发锁和幂等边界",
        "时间边界": "时间边界",
        "数值边界": "数值边界",
        "计算规则": "计算规则",
        "业务条件": "关键条件",
        "积分计算": "积分计算",
        "档位奖励": "档位奖励",
        "队列流转": "队列流转",
        "计数与状态变更": "计分或状态变更",
        "奖励派发": "奖励派发",
        "失败重试": "失败重试",
        "清理动作": "清理动作",
        "Redis Key": "Redis Key",
        "数据读取": "数据读取",
        "返回结构": "返回结构",
    }
    grouped: Dict[str, List[str]] = {}
    for fact in facts:
        category = str(fact.get("category") or "")
        statement = str(fact.get("statement") or "")
        snippet = statement.rsplit("源码：", 1)[-1].strip()
        if not category or not snippet:
            continue
        bucket = grouped.setdefault(category, [])
        if snippet not in bucket:
            bucket.append(snippet)
    parts = []
    for category, snippets in grouped.items():
        label = category_labels.get(category, category)
        representatives = _representative_fact_snippets(category, snippets)
        parts.append(f"{label}包括 " + "；".join(f"`{item}`" for item in representatives))
        if len(parts) >= 6:
            break
    target = "、".join(class_names) if class_names else "目标类"
    source_scope = "目标类及已验证直接基类" if inherited_files else "目标类"
    # 无显式类名时，检索层只能证明当前证据来自目标类，不能再拼出
    # “目标类的目标类”。有类名时则同时展示目标类与父类使用范围。
    source_description = f"{target} 的{source_scope}" if class_names else source_scope
    if not parts:
        return f"已从{source_description}定位业务控制点。"
    return (
        f"已从{source_description}确认以下类内机制："
        + "；".join(parts)
        + "。这些片段回答类内部如何执行，类外调用顺序和运行态数据仍需结合调用方与配置验证。"
    )
