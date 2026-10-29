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

    suggested_next_steps = []
    if evidence:
        suggested_next_steps.append("使用已定位文件和行号检查条件判断、异常分支与实际返回路径。")
    if result.get("configs"):
        suggested_next_steps.append("在目标环境只读核对配置键的实际值是否与代码预期一致。")
    if result.get("sql"):
        suggested_next_steps.append("使用只读 SQL 参数核对相关业务表记录、状态和关键时间字段。")
    if result.get("call_chains"):
        suggested_next_steps.append("沿报告中的调用链确认错误码来源和上层调用方。")
    if not successes:
        suggested_next_steps.append("补充数据库、配置或日志的按需只读验证计划以确认运行态事实。")
    if failures or warnings:
        suggested_next_steps.append("先处理未验证项和警告，再判断候选原因是否成立。")
    if not suggested_next_steps:
        suggested_next_steps.append("补充错误码、类方法、配置键或业务对象后重新诊断。")

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
        "suggested_next_steps": suggested_next_steps,
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
            "## 建议下一步",
            "",
            _lines(assessment["suggested_next_steps"]),
            "",
            "## AI 诊断叙写",
            "",
            _ai_diagnosis_lines(result.get("ai_diagnosis")),
            "",
            "## 检索关键词",
            "",
            _lines(result.get("queries") or []),
            "",
            "## 符号证据",
            "",
            _lines(
                f"{item.get('symbol_type')} {item.get('symbol_name')} {item.get('file')}:{item.get('line')}"
                for item in result.get("symbols") or []
            ),
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
            "## 配置线索",
            "",
            _lines(
                f"{item.get('key')} {item.get('file')}:{item.get('line')}"
                for item in result.get("configs") or []
            ),
            "",
            "## SQL 线索",
            "",
            _lines(
                f"{item.get('operation')} {item.get('content')} {item.get('file')}:{item.get('line')}"
                for item in result.get("sql") or []
            ),
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


def _ai_diagnosis_lines(diagnosis: Any) -> str:
    """格式化经过校验的 AI 诊断结果，不存在时给出明确状态。"""
    if not diagnosis:
        return "- 未启用或未生成 AI 诊断叙写"
    lines = ["结论：" + str(diagnosis.get("conclusion", ""))]
    for cause in diagnosis.get("possible_causes") or []:
        refs = ", ".join(str(item) for item in cause.get("evidence_refs") or [])
        lines.append(
            f"候选原因：{cause.get('title', '')}；依据：{cause.get('reasoning', '')}；证据：{refs}"
        )
    for uncertainty in diagnosis.get("uncertainties") or []:
        lines.append(f"不确定项：{uncertainty}")
    for action in diagnosis.get("recommended_actions") or []:
        lines.append(f"建议：{action}")
    return _lines(lines)
