"""诊断报告的稳定 JSON 摘要和 Markdown 导出格式。"""

from typing import Any, Dict, Iterable


def _lines(items: Iterable[Any]) -> str:
    """把列表内容转换为稳定的 Markdown 无序列表。"""
    values = [str(item) for item in items]
    return "\n".join(f"- {value}" for value in values) or "- 无"


def _validation_items(validation: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """展开数据库、配置和日志验证结果，便于统一统计。"""
    database = validation.get("database") or {}
    if database.get("status") not in {None, "not_requested"}:
        yield {"type": "数据库", **database}
    for config in validation.get("configs") or []:
        yield {"type": "配置", **config}
    for log in validation.get("logs") or []:
        yield {"type": "日志", **log}


def build_assessment(result: Dict[str, Any]) -> Dict[str, Any]:
    """根据证据和验证状态生成可解释的结论、置信度和未验证项。"""
    evidence = result.get("evidence") or []
    warnings = [str(item) for item in result.get("warnings") or []]
    validation_items = list(_validation_items(result.get("validation") or {}))
    successes = [item for item in validation_items if item.get("status") == "SUCCESS"]
    failures = [item for item in validation_items if item.get("status") == "FAILED"]

    confirmed_facts = []
    for item in evidence:
        confirmed_facts.append(f"代码证据：{item.get('file')}:{item.get('line')}")
    for item in successes:
        label = "查询成功" if item["type"] == "数据库" else f"{item.get('file', '')} 读取成功"
        confirmed_facts.append(f"{item['type']}验证：{label}")

    unverified_items = warnings
    for item in failures:
        unverified_items.append(f"{item['type']}验证失败：{item.get('error', '未知错误')}")
    if not evidence:
        unverified_items.append("未定位到相关代码证据")
    if not successes:
        unverified_items.append("未成功完成数据库、配置或日志事实验证")

    if evidence and successes and not failures and not warnings:
        level, label, score = "high", "高", 0.85
        conclusion = "已定位相关代码证据并完成事实验证，可依据已确认事实继续排查。"
        reason = "存在代码证据，事实验证成功，且没有失败或警告。"
    elif evidence or successes:
        level, label, score = "medium", "中", 0.55
        conclusion = "已获得部分代码或事实证据，但仍需人工确认未验证项。"
        reason = "证据或验证结果不完整，存在未验证内容。"
    else:
        level, label, score = "low", "低", 0.2
        conclusion = "当前未找到足够的代码证据，暂时无法形成可靠诊断结论。"
        reason = "缺少代码证据和成功的事实验证。"

    return {
        "conclusion": conclusion,
        "confidence": {"level": level, "label": label, "score": score, "reason": reason},
        "confirmed_facts": confirmed_facts,
        "unverified_items": unverified_items,
    }


def build_markdown_report(task: Dict[str, Any]) -> str:
    """将诊断任务和结果转换为便于人工审阅的 Markdown 报告。"""
    result = task.get("result") or {}
    validation = result.get("validation") or {}
    warnings = result.get("warnings") or []
    evidence = result.get("evidence") or []
    assessment = build_assessment(result)
    return "\n".join(
        [
            f"# 诊断报告：{task.get('task_id', task.get('id', 'unknown'))}",
            "",
            f"- 项目：{task.get('project', '')}",
            f"- 问题：{task.get('question', '')}",
            f"- 状态：{task.get('status', '')}",
            f"- 环境：{task.get('environment', '')}",
            "",
            "## 结论",
            "",
            assessment["conclusion"],
            "",
            "## 置信度",
            "",
            f"{assessment['confidence']['label']}（{assessment['confidence']['score']:.2f}）：{assessment['confidence']['reason']}",
            "",
            "## 已确认事实",
            "",
            _lines(assessment["confirmed_facts"]),
            "",
            "## 未验证项",
            "",
            _lines(assessment["unverified_items"]),
            "",
            "## 检索关键词",
            "",
            _lines(result.get("queries") or []),
            "",
            "## 代码证据",
            "",
            _lines(
                f"{item.get('file')}:{item.get('line')} {item.get('content', '')}"
                for item in evidence
            ),
            "",
            "## 调用链",
            "",
            _lines(result.get("call_chains") or []),
            "",
            "## 事实验证",
            "",
            f"```json\n{_json_text(validation)}\n```",
            "",
            "## 警告",
            "",
            _lines(warnings),
        ]
    )


def _json_text(value: Any) -> str:
    """以非 ASCII 友好的稳定格式序列化报告中的验证摘要。"""
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
