"""证据约束 LLM 诊断提供方测试。"""

import json

import httpx
import pytest

from app.llm_diagnosis import (
    LLMConfigurationError,
    LLMResponseError,
    build_evidence_context,
    generate_ai_diagnosis,
    generate_ai_diagnosis_run,
    validate_llm_settings,
)
from app.settings import Settings


def _settings() -> Settings:
    """返回启用 LLM 的测试配置。"""
    return Settings(
        llm_enabled=True,
        llm_api_url="https://api.example.test/v1/responses",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_timeout_seconds=10,
    )


def _result() -> dict:
    """返回带代码和验证证据的诊断结果。"""
    return {
        "evidence": [{"file": "Guild.php", "line": 8, "content": "return 'BD_OP_FORBIDDEN';"}],
        "symbols": [{"symbol_name": "GuildController::inviteGuild", "file": "Guild.php", "line": 5}],
        "configs": [{"key": "anchor.limit", "file": "Guild.php", "line": 7}],
        "sql": [],
        "call_chains": [],
        "validation": {"database": {"status": "SUCCESS"}},
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_llm_structured_output_and_evidence_refs_are_validated() -> None:
    """验证 Responses API 请求使用 JSON Schema 并接受有效证据引用。"""
    output = {
        "conclusion": "配置条件可能触发禁止返回。",
        "possible_causes": [
            {
                "title": "Day limit condition",
                "reasoning": "证据显示超过限制会返回错误码。",
                "evidence_refs": ["Guild.php:8"],
            }
        ],
        "uncertainties": ["未验证运行时配置值。"],
        "recommended_actions": ["只读核对 anchor.limit。"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """验证请求结构并返回 Structured Outputs 响应。"""
        payload = json.loads(request.content)
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        assert payload["store"] is False
        assert payload["max_output_tokens"] == 2000
        assert "allowed_evidence_refs" in json.loads(payload["input"])
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(output),
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        diagnosis = await generate_ai_diagnosis(
            _settings(), "waky3", "为什么邀请失败", _result(), client=client
        )
    assert diagnosis.possible_causes[0].evidence_refs == ["Guild.php:8"]


@pytest.mark.asyncio
async def test_llm_run_records_latency_and_token_usage() -> None:
    """验证调用元数据记录模型、协议、证据数、耗时和 token。"""
    output = {
        "conclusion": "结论。",
        "possible_causes": [],
        "uncertainties": [],
        "recommended_actions": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """返回带 usage 的 Chat Completions 响应。"""
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
            },
        )

    settings = _settings()
    settings.llm_api_url = "https://ark.example.test/api/coding/v3"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        run = await generate_ai_diagnosis_run(
            settings, "waky3", "问题", _result(), client=client
        )
    assert run.metadata["model"] == "test-model"
    assert run.metadata["protocol"] == "chat_completions"
    assert run.metadata["usage"]["total_tokens"] == 100
    assert run.metadata["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_llm_rejects_unknown_evidence_reference() -> None:
    """验证模型不能引用上下文中不存在的文件和行号。"""
    output = {
        "conclusion": "候选原因。",
        "possible_causes": [
            {"title": "x", "reasoning": "y", "evidence_refs": ["Missing.php:99"]}
        ],
        "uncertainties": [],
        "recommended_actions": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """返回包含伪造证据的模型输出。"""
        return httpx.Response(200, json={"output_text": json.dumps(output)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMResponseError, match="不存在的证据"):
            await generate_ai_diagnosis(
                _settings(), "waky3", "问题", _result(), client=client
            )


def test_llm_configuration_and_evidence_context_are_bounded() -> None:
    """验证启用时缺少模型或密钥失败，证据内容会截断。"""
    with pytest.raises(LLMConfigurationError):
        validate_llm_settings(Settings(llm_enabled=True, llm_model="", llm_api_key=""))
    with pytest.raises(LLMConfigurationError):
        validate_llm_settings(Settings(llm_enabled=True, llm_model="m", llm_api_key="k", llm_min_evidence_items=-1))
    result = _result()
    result["evidence"][0]["content"] = "x" * 5000
    context = build_evidence_context("waky3", "问题", result)
    assert len(context["evidence"][0]["content"]) == 1000
    assert context["allowed_evidence_refs"] == ["Guild.php:5", "Guild.php:7", "Guild.php:8"]


@pytest.mark.asyncio
async def test_ark_base_url_uses_chat_completions_protocol() -> None:
    """验证方舟 Base URL 自动追加 chat/completions 并解析消息内容。"""
    output = {
        "conclusion": "候选结论。",
        "possible_causes": [
            {"title": "x", "reasoning": "y", "evidence_refs": ["Guild.php:8"]}
        ],
        "uncertainties": [],
        "recommended_actions": [],
    }
    settings = _settings()
    settings.llm_api_url = "https://ark.example.test/api/coding/v3"

    def handler(request: httpx.Request) -> httpx.Response:
        """验证请求路径和 Chat Completions 请求体。"""
        assert str(request.url) == "https://ark.example.test/api/coding/v3/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(output)}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        diagnosis = await generate_ai_diagnosis(
            settings, "waky3", "问题", _result(), client=client
        )
    assert diagnosis.conclusion == "候选结论。"
