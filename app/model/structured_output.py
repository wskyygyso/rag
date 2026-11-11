"""构造供应商请求并校验证据约束的 Structured Outputs 响应。"""

import json
from typing import Any, Dict, Set

from app.model.contracts import AIDiagnosis
from app.model.errors import LLMResponseError
from app.model.routing import ModelTarget


def structured_output_schema() -> Dict[str, Any]:
    """返回严格诊断 JSON Schema。

    Returns:
        Pydantic 生成的 JSON Schema；额外字段被禁止，结论、原因、不确定性和建议
        均有长度上限。
    """
    return AIDiagnosis.model_json_schema()


def build_model_payload(
    target: ModelTarget,
    instructions: str,
    context: Dict[str, Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """为 Responses API 或 Chat Completions 构造受限请求体。

    参数:
        - `target`：本轮模型、协议和 Endpoint，决定请求体协议字段。
        - `instructions`：系统安全边界和证据约束说明。
        - `context`：已裁剪、脱敏并包含允许引用集合的诊断上下文。
        - `schema`：严格诊断输出 JSON Schema。

    Returns:
        可直接发送给供应商的 JSON 字典；不包含 API Key，密钥只放在 HTTP 头。
    """
    serialized_context = json.dumps(context, ensure_ascii=False)
    if target.protocol == "responses":
        return {
            "model": target.model,
            "instructions": instructions,
            "input": serialized_context,
            "store": False,
            "max_output_tokens": 2000,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "php_diagnosis",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
    return {
        "model": target.model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": serialized_context},
        ],
        "max_tokens": 2000,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "php_diagnosis",
                "strict": True,
                "schema": schema,
            },
        },
    }


def extract_output_text(target: ModelTarget, payload: Dict[str, Any]) -> str:
    """按供应商协议提取模型正文。

    参数:
        - `target`：当前模型目标，决定读取 Responses 或 Chat Completions 结构。
        - `payload`：供应商返回且已解析的 JSON 字典。

    Returns:
        待执行诊断 Schema 校验的文本。

    Raises:
        LLMResponseError：响应缺少正文、正文为空或结构不符合所选协议时抛出。
    """
    if not isinstance(payload, dict):
        raise LLMResponseError("LLM 响应 JSON 顶层必须是对象")
    text = _extract_responses_text(payload) if target.protocol == "responses" else _extract_chat_text(payload)
    if not text.strip():
        raise LLMResponseError("LLM 响应正文为空")
    return text


def _extract_responses_text(payload: Dict[str, Any]) -> str:
    """从 Responses API 载荷提取全部 output_text。

    参数:
        - `payload`：Responses API 返回的 JSON 字典。

    Returns:
        顶层 output_text，或各 output 内容块的文本拼接结果。

    Raises:
        LLMResponseError：响应不包含可识别的 output_text 内容块时抛出。
    """
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    texts = []
    for output in payload.get("output") or []:
        for content in output.get("content") or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if not texts:
        raise LLMResponseError("LLM 响应不包含 output_text")
    return "\n".join(texts)


def _extract_chat_text(payload: Dict[str, Any]) -> str:
    """从 OpenAI-compatible Chat Completions 载荷提取消息正文。

    参数:
        - `payload`：Chat Completions 返回的 JSON 字典。

    Returns:
        第一条 choice 的字符串正文，或文本内容块拼接结果。

    Raises:
        LLMResponseError：choices、message.content 或内容块结构无效时抛出。
    """
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("LLM 响应不包含 chat completion content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            return "".join(texts)
    raise LLMResponseError("LLM chat completion content 格式无效")


def validate_structured_diagnosis(text: str, allowed_refs: Set[str]) -> AIDiagnosis:
    """解析诊断 JSON 并拒绝上下文之外的证据引用。

    参数:
        - `text`：模型返回的诊断 JSON 文本。
        - `allowed_refs`：证据上下文明确允许引用的文件和行号集合。

    Returns:
        通过 Schema、长度和引用边界校验的 AI 诊断。

    Raises:
        LLMResponseError：JSON 或字段不符合 Schema，或任一候选原因引用了不存在的
        证据时抛出。调用方不得把异常文本当成已证实诊断。
    """
    try:
        diagnosis = AIDiagnosis.model_validate_json(text)
    except Exception as exc:
        raise LLMResponseError("LLM 输出不符合诊断 JSON Schema") from exc
    invalid_refs = {
        ref
        for cause in diagnosis.possible_causes
        for ref in cause.evidence_refs
        if ref not in allowed_refs
    }
    if invalid_refs:
        raise LLMResponseError(f"LLM 引用了不存在的证据：{sorted(invalid_refs)}")
    return diagnosis


def normalize_usage(payload: Dict[str, Any]) -> Dict[str, int]:
    """提取受控 token 统计字段。

    参数:
        - `payload`：供应商响应 JSON，顶层 `usage` 可包含不同协议字段。

    Returns:
        仅包含非负数值 token 字段的整数摘要；其他供应商扩展字段被丢弃。
    """
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    allowed = {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
    return {
        key: int(value)
        for key, value in usage.items()
        if key in allowed and isinstance(value, (int, float)) and value >= 0
    }
