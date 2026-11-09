"""诊断报告的稳定 JSON 摘要和 Markdown 导出格式。"""

import re
from typing import Any, Dict, Iterable


_CONTEXT_LINE_PATTERN = re.compile(r"^\s*(\d+):\s?(.*)$")


def _lines(items: Iterable[Any]) -> str:
    """把列表内容转换为稳定的 Markdown 无序列表。

    参数:
        - `items`：待格式化的条目集合，类型为 `Iterable[Any]`。
    """
    values = [str(item) for item in items]
    return "\n".join(f"- {value}" for value in values) or "- 无"


def _validation_items(validation: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """展开数据库、配置和日志验证结果，便于统一统计。

    参数:
        - `validation`：只读事实验证结果，类型为 `Dict[str, Any]`。
    """
    database = validation.get("database") or {}
    if database.get("status") not in {None, "not_requested"}:
        yield {"type": "数据库", **database}
    for config in validation.get("configs") or []:
        yield {"type": "配置", **config}
    for log in validation.get("logs") or []:
        yield {"type": "日志", **log}


def build_assessment(result: Dict[str, Any]) -> Dict[str, Any]:
    """根据证据和验证状态生成可解释的结论、置信度和未验证项。

    参数:
        - `result`：诊断 Worker 生成的结构化结果，包含证据、事实验证和警告，类型为 `Dict[str, Any]`。
    """
    evidence = result.get("evidence") or []
    deterministic = result.get("deterministic_findings") or {}
    deterministic_facts = deterministic.get("facts") or []
    warnings = [str(item) for item in result.get("warnings") or []]
    validation_items = list(_validation_items(result.get("validation") or {}))
    successes = [item for item in validation_items if item.get("status") == "SUCCESS"]
    failures = [item for item in validation_items if item.get("status") == "FAILED"]

    confirmed_facts = []
    for fact in deterministic_facts:
        refs = ", ".join(str(ref) for ref in fact.get("evidence_refs") or [])
        confirmed_facts.append(
            f"{fact.get('category', '业务约束')}：{fact.get('statement', '')}"
            + (f"（证据：{refs}）" if refs else "")
        )
    if not deterministic_facts:
        # 没有领域结论时才把原始代码命中作为确认线索。存在确定性事实时，
        # 每条事实已经携带经过筛选的源码引用；继续罗列全部候选命中会把礼物、
        # 活动和同名方法等低相关证据混入“已确认事实”，误导阅读者。
        for item in evidence:
            code_ref = f"代码证据：{item.get('file')}:{item.get('line')}"
            if code_ref not in confirmed_facts:
                confirmed_facts.append(code_ref)
    for item in successes:
        label = "查询成功" if item["type"] == "数据库" else f"{item.get('file', '')} 读取成功"
        confirmed_facts.append(f"{item['type']}验证：{label}")

    unverified_items = warnings
    for uncertainty in deterministic.get("uncertainties") or []:
        unverified_items.append(str(uncertainty))
    for item in failures:
        unverified_items.append(f"{item['type']}验证失败：{item.get('error', '未知错误')}")
    if not evidence:
        unverified_items.append("未定位到相关代码证据")
    if not successes:
        unverified_items.append("未成功完成数据库、配置或日志事实验证")

    suggested_next_steps = []
    if deterministic_facts:
        suggested_next_steps.append("沿确定性事实引用的文件和行号核对目标代码版本与运行分支。")
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

    deterministic_conclusion = str(deterministic.get("conclusion") or "").strip()
    if deterministic_conclusion and deterministic_facts:
        level, label, score = "medium", "中", 0.72
        conclusion = deterministic_conclusion
        reason = "已由源码证据生成确定性业务事实；运行环境状态仍需按需验证。"
        if evidence and successes and not failures and not warnings:
            level, label, score = "high", "高", 0.88
            reason = "已由源码证据确认业务约束，并完成事实验证且没有警告。"
    elif evidence and successes and not failures and not warnings:
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
    """将诊断任务和结果转换为便于人工审阅的 Markdown 报告。

    参数:
        - `task`：诊断任务记录，类型为 `Dict[str, Any]`。
    """
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
            "## 确定性业务事实",
            "",
            _deterministic_fact_lines(result.get("deterministic_findings")),
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


def build_evidence_markdown(task: Dict[str, Any]) -> str:
    """构建以业务限制和源码证据为核心的 Markdown 审阅文件。

    参数:
        - `task`：已完成任务的稳定 API 响应，必须包含任务元数据、诊断结果和
          `assessment`；证据文件只陈述源码可证明的事实，并把运行态验证与
          AI 补充单独列出，避免审阅人把未执行的环境验证误认为已确认。

    Returns:
        可直接下载或归档的 Markdown 文本。即使没有确定性事实或证据，
        也会返回带明确缺口的文件，而不是抛出业务异常。
    """
    result = task.get("result") or {}
    findings = result.get("deterministic_findings") or {}
    facts = findings.get("facts") or []
    assessment = task.get("assessment") or build_assessment(result)
    evidence = result.get("evidence") or []
    retrieval_plan = result.get("retrieval_plan") or {}
    evidence_by_ref = _evidence_index(evidence)
    lines = [
        f"# 诊断证据：{task.get('task_id', task.get('id', 'unknown'))}",
        "",
        f"- 项目：{task.get('project', '')}",
        f"- 问题：{task.get('question', '')}",
        f"- 环境：{task.get('environment', '')}",
        f"- 任务状态：{task.get('status', '')}",
        f"- 创建时间：{task.get('created_at', '')}",
        f"- 结果更新时间：{task.get('updated_at', '')}",
        "",
        "> 证据口径：业务限制来自源码确定性分析；数据库、配置和日志验证及 AI 叙写分别标注，未执行或失败的项目不能视为已确认。",
        "",
        "## 诊断结论",
        "",
        str(assessment.get("conclusion") or findings.get("conclusion") or "当前没有形成结论。"),
        "",
        "## 置信度",
        "",
        _confidence_line(assessment.get("confidence") or {}),
        "",
        "## 业务限制与源码依据",
        "",
    ]

    if facts:
        for index, fact in enumerate(facts, start=1):
            refs = [str(item) for item in fact.get("evidence_refs") or []]
            lines.extend(
                [
                    f"### {index}. {fact.get('category', '业务限制')}",
                    "",
                    str(fact.get("statement", "")).strip() or "未提供事实说明。",
                    "",
                    "源码引用：" + ("、".join(f"`{ref}`" for ref in refs) if refs else "无"),
                    "",
                ]
            )
            for ref in refs:
                item = evidence_by_ref.get(ref)
                if item is None:
                    lines.append(f"- `{ref}`：本次证据快照中未包含该行上下文。")
                    continue
                content = str(item.get("content") or "").strip()
                lines.append(f"- `{ref}`：{content or '该证据行没有可展示的源码文本。'}")
            lines.append("")
    else:
        lines.extend(["- 本次检索未生成确定性业务限制。", ""])

    lines.extend(["## 源码证据上下文", ""])
    if evidence:
        for item in evidence:
            ref = f"{item.get('file', '')}:{item.get('line', '')}"
            lines.extend(
                [
                    f"### `{ref}`",
                    "",
                    f"- 命中检索词：{item.get('query') or '未记录'}",
                    f"- 证据行：{str(item.get('content') or '').strip() or '无'}",
                    "",
                    "```text",
                    str(item.get("context") or item.get("content") or "无可用上下文").rstrip(),
                    "```",
                    "",
                ]
            )
    else:
        lines.extend(["未采集到源码证据。", ""])

    lines.extend(
        [
            "## 运行环境验证",
            "",
            _validation_markdown(result.get("validation") or {}),
            "",
            "## AI 补充",
            "",
            _ai_diagnosis_lines(result.get("ai_diagnosis")),
            "",
            "## 检索与关联线索",
            "",
            "### 检索关键词",
            "",
            _lines(result.get("queries") or []),
            "",
            "### 自动检索计划",
            "",
            f"- 通用业务片段：{retrieval_plan.get('generic_anchor_count', 0)} 个",
            f"- 通用源码/文档命中：{retrieval_plan.get('generic_hit_count', 0)} 条",
            f"- 动态发现扩展词：{len(retrieval_plan.get('discovered_queries') or [])} 个",
            "",
            _lines(
                f"`{item.get('query')}` ← {item.get('reason') or '通用命中'} "
                f"（{item.get('file') or '未记录'}:{item.get('line') or 0}）"
                for item in retrieval_plan.get("discovery_sources") or []
            ),
            "",
            "### 符号",
            "",
            _lines(
                f"{item.get('symbol_type')} {item.get('symbol_name')} {item.get('file')}:{item.get('line')}"
                for item in result.get("symbols") or []
            ),
            "",
            "### 配置线索",
            "",
            _lines(
                f"{item.get('key')} {item.get('file')}:{item.get('line')}"
                for item in result.get("configs") or []
            ),
            "",
            "### SQL 线索",
            "",
            _lines(
                f"{item.get('operation')} {item.get('content')} {item.get('file')}:{item.get('line')}"
                for item in result.get("sql") or []
            ),
            "",
            "## 未验证项与警告",
            "",
            _lines(
                list(assessment.get("unverified_items") or [])
                + [f"诊断警告：{item}" for item in result.get("warnings") or []]
            ),
            "",
            "## 建议下一步",
            "",
            _lines(assessment.get("suggested_next_steps") or []),
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _evidence_index(evidence: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """建立源码证据的精确行索引，覆盖命中行和多行上下文中的所有行。

    参数:
        - `evidence`：Worker 保存的代码证据，每项包含顶层命中行以及可选的多行
          `context`；确定性业务事实可能引用上下文中的判断行或返回值行。

    Returns:
        以“文件:行号”为键的证据字典。顶层命中行优先保留原始元数据，上下文行
        会生成带真实行号和行内容的轻量证据，避免已经用于事实分析的源码行在
        下载文件中被误报为“未包含上下文”。
    """
    index: Dict[str, Dict[str, Any]] = {}
    for item in evidence:
        file_name = str(item.get("file") or "")
        if not file_name:
            continue
        line = item.get("line")
        if line is not None:
            index.setdefault(f"{file_name}:{line}", item)
        for raw_line in str(item.get("context") or "").splitlines():
            match = _CONTEXT_LINE_PATTERN.match(raw_line)
            if not match:
                continue
            context_line = int(match.group(1))
            index.setdefault(
                f"{file_name}:{context_line}",
                {
                    **item,
                    "line": context_line,
                    "content": match.group(2),
                },
            )
    return index


def _confidence_line(confidence: Dict[str, Any]) -> str:
    """把置信度对象格式化为证据文件中的单行说明。

    参数:
        - `confidence`：评估阶段生成的置信级别、分数和原因；缺少字段时使用
          “未评估”占位，避免下载文件因历史任务结构不完整而失败。
    """
    label = confidence.get("label") or "未评估"
    score = confidence.get("score")
    reason = confidence.get("reason") or "本次任务没有可用的置信度说明。"
    score_text = f"（{float(score):.2f}）" if isinstance(score, (int, float)) else ""
    return f"{label}{score_text}：{reason}"


def _validation_markdown(validation: Dict[str, Any]) -> str:
    """汇总数据库、配置和日志的事实验证状态。

    参数:
        - `validation`：诊断 Worker 返回的只读验证结果；没有请求验证时返回
          明确提示，防止读者把源码事实误当成目标环境已经验证。
    """
    items = list(_validation_items(validation))
    if not items:
        return "- 本次未执行数据库、配置或日志事实验证。"
    lines = []
    for item in items:
        label = item.get("file") or item.get("key") or item.get("type")
        status = item.get("status") or "UNKNOWN"
        error = f"；原因：{item.get('error')}" if item.get("error") else ""
        lines.append(f"- {item.get('type')} {label}：{status}{error}")
    return "\n".join(lines)


def _json_text(value: Any) -> str:
    """以非 ASCII 友好的稳定格式序列化报告中的验证摘要。

    参数:
        - `value`：需要序列化为 JSON 文本的报告数据，类型为 `Any`。
    """
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _ai_diagnosis_lines(diagnosis: Any) -> str:
    """格式化经过校验的 AI 诊断结果，不存在时给出明确状态。

    参数:
        - `diagnosis`：AI 诊断结构化结果，类型为 `Any`。
    """
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


def _deterministic_fact_lines(findings: Any) -> str:
    """格式化不依赖模型的业务事实及其代码引用。

    参数:
        - `findings`：Worker 从已检索源码生成的确定性业务摘要，类型为 `Any`。

    Returns:
        Markdown 列表文本。没有确定性事实时明确说明未定位，避免把模型
        不可用或报告为空误解为业务规则不存在。
    """
    if not findings or not findings.get("facts"):
        return "- 未从本次检索证据生成确定性业务事实"
    lines = [f"结论：{findings.get('conclusion', '')}"]
    for fact in findings.get("facts") or []:
        refs = ", ".join(str(item) for item in fact.get("evidence_refs") or [])
        statement = str(fact.get("statement", "")).rstrip("。；;")
        lines.append(
            f"{fact.get('category', '业务约束')}：{statement}"
            + (f"；证据：{refs}" if refs else "")
        )
    for uncertainty in findings.get("uncertainties") or []:
        lines.append(f"不确定项：{uncertainty}")
    return _lines(lines)
