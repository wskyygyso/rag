"""带候选路由、错误分类和熔断的模型调用网关。"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.model.contracts import AIDiagnosisRun
from app.model.errors import ModelGatewayError, ModelInvocationError
from app.model.health import ModelHealthStore
from app.model.routing import ModelTarget, resolve_model_candidates
from app.model.structured_output import (
    build_model_payload,
    extract_output_text,
    normalize_usage,
    structured_output_schema,
    validate_structured_diagnosis,
)
from app.settings import Settings


class ModelGateway:
    """在有限预算内按顺序尝试候选模型并校验结构化诊断。

    网关只负责 AI 叙写。任何模型故障均以异常交给调用方降级，确定性代码检索、
    业务事实和报告落库不受网关状态影响。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        health_store: Optional[ModelHealthStore] = None,
    ) -> None:
        """初始化模型网关。

        参数:
            - `settings`：模型候选、协议、超时和熔断配置。
            - `health_store`：可选 Redis 健康状态；为空时只执行进程内有限尝试。
        """
        self._settings = settings
        self._health_store = health_store

    async def generate(
        self,
        context: Dict[str, Any],
        instructions: str,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> AIDiagnosisRun:
        """调用候选模型并返回首个通过证据校验的诊断。

        参数:
            - `context`：已裁剪、脱敏的证据上下文和允许引用集合。
            - `instructions`：拒绝提示词注入并限制证据外推断的系统说明。
            - `client`：可复用的异步 HTTP 客户端；为空时由网关创建并关闭。

        Returns:
            最终成功模型的诊断，以及候选尝试、失败类型、耗时和 token 元数据。

        Raises:
            LLMResponseError：所有候选均不可用，或模型输出违反 Schema/证据边界时
            抛出。调用方必须保留确定性分析并记录 AI 降级，不得伪造 AI 结论。
        """
        targets = resolve_model_candidates(self._settings)
        max_attempts = min(self._settings.llm_max_attempts, len(targets))
        schema = structured_output_schema()
        allowed_refs = set(context.get("allowed_evidence_refs") or [])
        attempt_summaries: List[Dict[str, Any]] = []
        owns_client = client is None
        active_client = client or httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds)
        gateway_started_at = time.perf_counter()
        last_error: Optional[ModelInvocationError] = None
        try:
            network_attempts = 0
            for target in targets:
                if network_attempts >= max_attempts:
                    break
                if self._health_store is not None and await self._health_store.is_open(target):
                    attempt_summaries.append(
                        {
                            "model": target.model,
                            "protocol": target.protocol,
                            "status": "circuit_open",
                            "elapsed_ms": 0.0,
                            "error_type": "circuit_open",
                        }
                    )
                    continue
                network_attempts += 1
                started_at = time.perf_counter()
                try:
                    payload = build_model_payload(target, instructions, context, schema)
                    diagnosis, usage = await self._call_target(
                        active_client,
                        target,
                        payload,
                        allowed_refs,
                    )
                except ModelInvocationError as exc:
                    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
                    attempt_summaries.append(
                        self._attempt_summary(target, "failed", elapsed_ms, exc)
                    )
                    last_error = exc
                    if exc.retryable and self._health_store is not None:
                        await self._health_store.record_failure(target)
                    if not exc.retryable:
                        break
                    continue
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
                attempt_summaries.append(
                    {
                        "model": target.model,
                        "protocol": target.protocol,
                        "status": "succeeded",
                        "elapsed_ms": elapsed_ms,
                    }
                )
                if self._health_store is not None:
                    await self._health_store.record_success(target)
                metadata = {
                    "model": target.model,
                    "protocol": target.protocol,
                    "evidence_count": len(allowed_refs),
                    "latency_ms": round((time.perf_counter() - gateway_started_at) * 1000, 3),
                    "usage": usage,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "candidate_attempts": attempt_summaries,
                    "attempt_count": network_attempts,
                    "status": "succeeded",
                    "degraded": network_attempts > 1,
                    "fallback_reason": (
                        attempt_summaries[-2].get("error_type", "")
                        if network_attempts > 1 and len(attempt_summaries) > 1
                        else ""
                    ),
                }
                return AIDiagnosisRun(diagnosis=diagnosis, metadata=metadata)
        finally:
            if owns_client:
                await active_client.aclose()
        error_type = last_error.error_type if last_error else "no_candidate"
        safe_message = last_error.safe_message if last_error else "没有可尝试的模型候选"
        raise ModelGatewayError(
            f"候选模型均不可用（{error_type}）：{safe_message}",
            {
                "status": "failed",
                "attempt_count": network_attempts,
                "candidate_attempts": attempt_summaries,
                "error_type": error_type,
                "error_message": safe_message,
            },
        )

    async def _call_target(
        self,
        client: httpx.AsyncClient,
        target: ModelTarget,
        payload: Dict[str, Any],
        allowed_refs: set,
    ):
        """执行一次模型 HTTP 调用并完成正文和证据校验。

        参数:
            - `client`：本次网关共享的异步 HTTP 客户端。
            - `target`：当前模型、协议和 Endpoint。
            - `payload`：不包含密钥的结构化请求体。
            - `allowed_refs`：模型允许引用的证据文件和行号集合。

        Returns:
            `(AIDiagnosis, usage)`；usage 仅包含受控 token 字段。

        Raises:
            ModelInvocationError：网络、HTTP、空响应或结构化输出校验失败时抛出，
              并使用错误分类决定是否切换候选。
        """
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await client.post(target.endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ModelInvocationError(
                error_type="timeout",
                safe_message="模型请求超时",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ModelInvocationError(
                error_type="connection_error",
                safe_message="模型网络连接失败",
                retryable=True,
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
            raise ModelInvocationError(
                error_type="rate_limited" if response.status_code == 429 else "http_error",
                safe_message=f"模型接口返回 HTTP {response.status_code}",
                retryable=retryable,
                status_code=response.status_code,
            )
        try:
            response_payload = response.json()
            text = extract_output_text(target, response_payload)
            diagnosis = validate_structured_diagnosis(text, allowed_refs)
            usage = normalize_usage(response_payload)
            return diagnosis, usage
        except (ValueError, TypeError) as exc:
            raise ModelInvocationError(
                error_type="invalid_response",
                safe_message=str(exc)[:300],
                retryable=True,
            ) from exc

    def _attempt_summary(
        self,
        target: ModelTarget,
        status: str,
        elapsed_ms: float,
        error: ModelInvocationError,
    ) -> Dict[str, Any]:
        """生成不含密钥、Endpoint 和响应正文的候选尝试摘要。

        参数:
            - `target`：本次尝试的模型目标。
            - `status`：稳定状态值，例如 `failed`。
            - `elapsed_ms`：本次请求耗时毫秒数。
            - `error`：分类后的模型调用错误。

        Returns:
            可写入任务 `ai_metadata` 的字典。
        """
        return {
            "model": target.model,
            "protocol": target.protocol,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "error_type": error.error_type,
            "status_code": error.status_code,
            "message": error.safe_message,
        }
