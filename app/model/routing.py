"""模型候选顺序和 Endpoint 协议解析。"""

from dataclasses import dataclass
from typing import List, Tuple

from app.model.errors import LLMConfigurationError
from app.settings import Settings


@dataclass(frozen=True)
class ModelTarget:
    """描述一次可尝试的模型、协议和完整请求地址。"""

    model: str
    protocol: str
    endpoint: str

    @property
    def health_key(self) -> str:
        """返回按 Endpoint 和模型隔离的熔断键。"""
        return f"{self.endpoint}|{self.model}"


def resolve_target(settings: Settings, model: str) -> ModelTarget:
    """按配置 URL 解析指定模型的请求协议和完整地址。

    参数:
        - `settings`：应用运行配置，提供 API URL 和协议选择。
        - `model`：本轮要尝试的主模型或备用模型标识，不能为空。

    Returns:
        可直接发起 POST 的模型目标。显式 `chat_completions` 或 Base URL 会自动
        追加 `/chat/completions`，以 `/responses` 结尾的 URL 使用 Responses API。

    Raises:
        LLMConfigurationError：模型为空、URL 非 HTTP(S) 或协议值不受支持时抛出。
    """
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise LLMConfigurationError("LLM 模型标识不能为空")
    endpoint = settings.llm_api_url.rstrip("/")
    if not endpoint.startswith(("https://", "http://")):
        raise LLMConfigurationError("LLM_API_URL 必须使用 HTTP(S) URL")
    protocol = settings.llm_api_protocol
    if protocol == "auto":
        protocol = "responses" if endpoint.endswith("/responses") else "chat_completions"
    if protocol not in {"responses", "chat_completions"}:
        raise LLMConfigurationError("LLM_API_PROTOCOL 必须是 auto、responses 或 chat_completions")
    if protocol == "chat_completions" and not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    return ModelTarget(model=normalized_model, protocol=protocol, endpoint=endpoint)


def resolve_model_candidates(settings: Settings) -> Tuple[ModelTarget, ...]:
    """返回按优先级去重后的模型候选目标。

    参数:
        - `settings`：应用运行配置，主模型排在首位，`llm_candidate_models`
          中逗号分隔的备用模型保持声明顺序。

    Returns:
        去重后的模型目标元组。空候选被忽略，重复模型只保留首次出现的位置。

    Raises:
        LLMConfigurationError：主模型为空或任一候选 URL 配置无效时抛出。
    """
    configured = [settings.llm_model]
    configured.extend(part.strip() for part in settings.llm_candidate_models.split(","))
    targets: List[ModelTarget] = []
    seen = set()
    for model in configured:
        normalized = str(model or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        targets.append(resolve_target(settings, normalized))
    if not targets:
        raise LLMConfigurationError("启用 LLM 时必须至少配置一个模型")
    return tuple(targets)
