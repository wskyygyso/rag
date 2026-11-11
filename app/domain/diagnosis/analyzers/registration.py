"""设备注册数量限制事实分析器。

分析器只读取 `EvidenceLine` 和问题画像，不负责检索或持久化。
"""

import re
from typing import Any, Dict, List, Sequence, Tuple

from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.query_understanding import detect_semantic_concepts


def _analyze_device_registration_limit(
    lines: Sequence[EvidenceLine],
) -> List[Dict[str, Any]]:
    """从源码证据中提取设备注册数量的配置值、计数来源和拦截条件。

    参数:
        - `lines`：本次检索展开后的源码上下文，类型为 `Sequence[EvidenceLine]`。
          配置定义、注册计数查询和阈值判断通常位于不同文件，因此本函数只组合同
          一次证据快照中已经出现的源码行，不猜测缺失值。

    Returns:
        设备注册上限、计数来源和注册拦截事实。配置值是唯一答案来源；没有配置
        赋值或没有注册计数校验时返回不完整事实，调用方不得把相似的表名或字段名
        当成业务上限。函数不依赖项目名、完整问句、类名或表名。
    """
    config_pattern = re.compile(
        r"['\"](?P<key>[A-Za-z_][A-Za-z0-9_.]*)['\"]\s*=>\s*"
        r"(?P<value>\d+\b|getByTestMode\s*\(\s*\d+\s*,\s*\d+\s*\))",
        re.IGNORECASE,
    )
    config_entries: List[Tuple[EvidenceLine, str, str, int, int]] = []
    for item in lines:
        for match in config_pattern.finditer(item.content):
            key = match.group("key")
            folded_key = key.casefold()
            if "device" not in folded_key:
                continue
            if not any(marker in folded_key for marker in ("count", "limit", "max")):
                continue
            expression = match.group("value")
            if expression.isdigit():
                value = int(expression)
                config_entries.append((item, key, expression, value, value))
                continue
            values = re.findall(r"\d+", expression)
            if len(values) >= 2:
                config_entries.append(
                    (item, key, expression, int(values[0]), int(values[1]))
                )

    if not config_entries:
        return []

    facts: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()
    test_mode_helper = next(
        (
            item
            for item in lines
            if re.search(r"function\s+getByTestMode\s*\(", item.content, re.IGNORECASE)
            and "$testModeVal" in item.content
            and "$noTestModeVal" in item.content
        ),
        None,
    )
    for config_line, config_key, expression, test_value, production_value in config_entries[:3]:
        context_refs = [config_line.ref]
        for nearby in lines:
            if nearby.file != config_line.file:
                continue
            if abs(nearby.line - config_line.line) > 4:
                continue
            concepts = {
                concept.name for concept in detect_semantic_concepts(nearby.content)
            }
            if {"device", "registration"}.issubset(concepts):
                context_refs.append(nearby.ref)
        if test_mode_helper is not None:
            context_refs.append(test_mode_helper.ref)
        config_refs = list(dict.fromkeys(context_refs))
        if expression.isdigit():
            facts.append(
                {
                    "category": "设备注册上限",
                    "statement": (
                        f"配置项 `{config_key} = {production_value}` 定义了单个设备允许"
                        f"注册的用户数量上限为 {production_value}。"
                    ),
                    "evidence_refs": config_refs,
                }
            )
        else:
            facts.append(
                {
                    "category": "测试环境设备注册上限",
                    "statement": (
                        f"配置项 `{config_key} = {expression}`；按 `getByTestMode` 的"
                        f"首参数语义，测试环境允许单个设备注册的用户数量上限为"
                        f" {test_value}，达到或超过后拦截。"
                    ),
                    "evidence_refs": config_refs,
                }
            )
            facts.append(
                {
                    "category": "正式环境设备注册上限",
                    "statement": (
                        f"配置项 `{config_key} = {expression}`；按 `getByTestMode` 的"
                        f"第二参数语义，正式环境允许单个设备注册的用户数量上限为"
                        f" {production_value}。"
                    ),
                    "evidence_refs": config_refs,
                }
            )
        seen_refs.update(config_refs)

        key_leaf = config_key.rsplit(".", 1)[-1]
        matching_guards = [
            item
            for item in lines
            if key_leaf in item.content
            and any(operator in item.content for operator in (">=", ">", "==", "<=", "<"))
        ]
        for guard in matching_guards[:3]:
            if guard.ref in seen_refs:
                continue
            comparison = "达到或超过" if ">=" in guard.content else "触发"
            facts.append(
                {
                    "category": "注册拦截条件",
                    "statement": (
                        f"设备注册计数{comparison}配置上限时执行拦截：{guard.content.strip()}"
                    ),
                    "evidence_refs": [config_line.ref, guard.ref],
                }
            )
            seen_refs.add(guard.ref)

    count_lines = [
        item
        for item in lines
        if re.search(
            r"(?:device.{0,60}(?:reg|register).{0,60}count|reg_count.{0,60}device)",
            item.content,
            re.IGNORECASE,
        )
        and not re.search(r"\$logger|log(?:info|warn|notice)|echo\b", item.content, re.IGNORECASE)
    ]
    for item in count_lines[:4]:
        if item.ref in seen_refs:
            continue
        facts.append(
            {
                "category": "注册计数来源",
                "statement": f"设备注册次数从以下源码计数链路读取：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )
        seen_refs.add(item.ref)

    send_code_guards = [
        item
        for item in lines
        if (
            re.search(r"\bif\s*\([^\n]*regcount", item.content, re.IGNORECASE)
            and any(
                marker in item.content.casefold()
                for marker in ("config::get", "limit", "上限", "限制")
            )
        )
        and not re.search(r"\$logger|log(?:info|warn|notice)|echo\b", item.content, re.IGNORECASE)
    ]
    for item in send_code_guards[:4]:
        facts.append(
            {
                "category": "短信注册前置校验",
                "statement": f"发送注册短信前复用设备注册数量校验：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )

    registration_guards = [
        item
        for item in lines
        if re.search(
            r"\b(?:public\s+)?function\s+checkregcount\s*\(",
            item.content,
            re.IGNORECASE,
        )
    ]
    for item in registration_guards[:2]:
        if item.ref in seen_refs:
            continue
        facts.append(
            {
                "category": "注册入口校验",
                "statement": f"普通注册流程调用设备注册数量校验入口：{item.content.strip()}",
                "evidence_refs": [item.ref],
            }
        )
        seen_refs.add(item.ref)

    return facts[:12]
