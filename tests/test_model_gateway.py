"""模型网关候选路由、错误分类和熔断边界测试。"""

import json

import httpx
import pytest

from app.llm_diagnosis import LLMConfigurationError, LLMResponseError, generate_ai_diagnosis_run, validate_llm_settings
from app.model.health import ModelHealthStore
from app.model.errors import ModelGatewayError
from app.settings import Settings


def _settings(**overrides) -> Settings:
    """返回启用模型网关的测试配置。

    参数:
        - `overrides`：测试需要覆盖的字段；未指定字段使用可快速失败的安全默认值。

    Returns:
        已完成 Pydantic 校验的不可变入口配置对象。
    """
    values = {
        "llm_enabled": True,
        "llm_api_url": "https://api.example.test/v1/responses",
        "llm_api_key": "super-secret-key",
        "llm_model": "primary-model",
        "llm_timeout_seconds": 5,
        "llm_max_attempts": 2,
        "llm_circuit_failure_threshold": 1,
        "llm_circuit_open_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


def _result() -> dict:
    """返回满足最小 AI 调用门禁的证据结果。"""
    return {"evidence": [{"file": "Service.php", "line": 12, "content": "return false;"}]}


def _output(conclusion: str = "已根据源码确认。") -> dict:
    """构造通过严格 Schema 和证据引用校验的模型输出。

    参数:
        - `conclusion`：测试需要核对的最终结论文本。

    Returns:
        包含一条合法证据候选原因的诊断字典。
    """
    return {
        "conclusion": conclusion,
        "possible_causes": [
            {
                "title": "源码条件",
                "reasoning": "证据显示该条件直接返回 false。",
                "evidence_refs": ["Service.php:12"],
            }
        ],
        "uncertainties": [],
        "recommended_actions": ["只读核对运行时配置。"],
    }


class _HealthRedis:
    """保存熔断键的异步 Redis 测试替身。"""

    def __init__(self) -> None:
        """初始化空键空间和写入记录。"""
        self.values = {}
        self.set_calls = []

    async def get(self, key: str):
        """读取熔断状态。

        参数:
            - `key`：模型健康状态 Redis 键。
        """
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """保存带过期时间的状态。

        参数:
            - `key`：模型健康状态 Redis 键。
            - `ttl`：Redis 过期秒数。
            - `value`：序列化后的健康状态。
        """
        self.values[key] = value
        self.set_calls.append((key, ttl, value))

    async def delete(self, key: str) -> None:
        """删除指定健康状态。

        参数:
            - `key`：模型健康状态 Redis 键。
        """
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_gateway_falls_back_to_next_model_and_records_attempts() -> None:
    """验证主模型限流时按顺序切换备用模型且元数据不含密钥。"""
    settings = _settings(llm_candidate_models="backup-model")
    requested_models = []

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟主模型限流和备用模型成功。

        参数:
            - `request`：网关发出的模型 HTTP 请求。
        """
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        if payload["model"] == "primary-model":
            return httpx.Response(429, json={"error": {"message": "limited"}})
        return httpx.Response(200, json={"output_text": json.dumps(_output("备用模型结论"))})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        run = await generate_ai_diagnosis_run(
            settings,
            "waky6",
            "为什么业务被限制？",
            _result(),
            client=client,
        )

    assert requested_models == ["primary-model", "backup-model"]
    assert run.diagnosis.conclusion == "备用模型结论"
    assert run.metadata["model"] == "backup-model"
    assert run.metadata["attempt_count"] == 2
    assert run.metadata["degraded"] is True
    assert run.metadata["candidate_attempts"][0]["error_type"] == "rate_limited"
    assert "super-secret-key" not in json.dumps(run.metadata, ensure_ascii=False)


@pytest.mark.asyncio
async def test_gateway_tries_backup_when_primary_returns_invalid_evidence() -> None:
    """验证主模型伪造证据时不会采用其结论，并允许备用模型重新生成。"""
    settings = _settings(llm_candidate_models="backup-model")
    invalid_output = _output("伪造结论")
    invalid_output["possible_causes"][0]["evidence_refs"] = ["Missing.php:99"]

    def handler(request: httpx.Request) -> httpx.Response:
        """主模型返回非法引用，备用模型返回合法引用。

        参数:
            - `request`：网关发出的模型 HTTP 请求。
        """
        payload = json.loads(request.content)
        output = invalid_output if payload["model"] == "primary-model" else _output("备用结论")
        return httpx.Response(200, json={"output_text": json.dumps(output)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        run = await generate_ai_diagnosis_run(
            settings,
            "waky6",
            "业务限制是什么？",
            _result(),
            client=client,
        )

    assert run.diagnosis.conclusion == "备用结论"
    assert run.metadata["candidate_attempts"][0]["error_type"] == "invalid_response"
    assert "不存在的证据" in run.metadata["candidate_attempts"][0]["message"]


@pytest.mark.asyncio
async def test_model_circuit_skips_repeatedly_failing_primary_endpoint() -> None:
    """验证达到连续失败阈值后，后续请求不再等待同一模型超时。"""
    redis = _HealthRedis()
    settings = _settings()
    store = ModelHealthStore(
        redis,
        failure_threshold=settings.llm_circuit_failure_threshold,
        open_seconds=settings.llm_circuit_open_seconds,
    )
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        """持续返回服务端错误以触发熔断。

        参数:
            - `request`：网关发出的模型 HTTP 请求。
        """
        nonlocal requests
        requests += 1
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMResponseError):
            await generate_ai_diagnosis_run(
                settings,
                "waky6",
                "业务限制是什么？",
                _result(),
                client=client,
                health_store=store,
            )
        with pytest.raises(LLMResponseError, match="no_candidate"):
            await generate_ai_diagnosis_run(
                settings,
                "waky6",
                "业务限制是什么？",
                _result(),
                client=client,
                health_store=store,
            )

    assert requests == 1


def test_llm_attempt_budget_must_fit_inside_task_soft_timeout() -> None:
    """验证模型最坏总等待时间不能吞掉任务报告落库预算。"""
    settings = _settings(
        llm_timeout_seconds=90,
        llm_max_attempts=4,
        diagnosis_task_soft_time_limit_seconds=240,
    )

    with pytest.raises(LLMConfigurationError, match="低于诊断任务软超时"):
        validate_llm_settings(settings)


@pytest.mark.asyncio
async def test_gateway_exposes_safe_failure_metadata_for_deterministic_fallback() -> None:
    """验证全部候选失败时返回错误分类且不泄露密钥或完整响应正文。"""
    settings = _settings()
    invalid_output = _output("非法结论")
    invalid_output["possible_causes"][0]["evidence_refs"] = ["Missing.php:99"]

    def handler(request: httpx.Request) -> httpx.Response:
        """返回引用不存在证据的非法结构化输出。

        参数:
            - `request`：网关发出的模型 HTTP 请求。
        """
        return httpx.Response(200, json={"output_text": json.dumps(invalid_output)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelGatewayError) as caught:
            await generate_ai_diagnosis_run(
                settings,
                "waky6",
                "业务限制是什么？",
                _result(),
                client=client,
            )

    metadata = caught.value.metadata
    assert metadata["status"] == "failed"
    assert metadata["error_type"] == "invalid_response"
    assert "不存在的证据" in metadata["error_message"]
    assert "super-secret-key" not in json.dumps(metadata, ensure_ascii=False)
