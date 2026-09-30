"""诊断报告的稳定 JSON 摘要和 Markdown 导出格式。"""

from typing import Any, Dict, Iterable


def _lines(items: Iterable[Any]) -> str:
    """把列表内容转换为稳定的 Markdown 无序列表。"""
    values = [str(item) for item in items]
    return "\n".join(f"- {value}" for value in values) or "- 无"


def build_markdown_report(task: Dict[str, Any]) -> str:
    """将诊断任务和结果转换为便于人工审阅的 Markdown 报告。"""
    result = task.get("result") or {}
    validation = result.get("validation") or {}
    warnings = result.get("warnings") or []
    evidence = result.get("evidence") or []
    return "\n".join(
        [
            f"# 诊断报告：{task.get('task_id', task.get('id', 'unknown'))}",
            "",
            f"- 项目：{task.get('project', '')}",
            f"- 问题：{task.get('question', '')}",
            f"- 状态：{task.get('status', '')}",
            f"- 环境：{task.get('environment', '')}",
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
