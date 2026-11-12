"""文本向量提供方和真实语义检索所需的基础工具。

该模块同时保留离线确定性向量与 OpenAI-compatible embedding 提供方。确定性
向量只用于测试和无外部模型时的功能降级，不能承担中英文语义召回；生产检索应
显式启用真实 embedding。所有提供方只负责生成向量，不读取代码、不生成诊断
结论，向量命中仍须回到当前源码验证。
"""

import hashlib
import math
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Protocol

import httpx

EMBEDDING_DIMENSION = 64
EMBEDDING_MODEL = "hash-v1"
DEFAULT_REAL_EMBEDDING_MODEL = "text-embedding-3-small"
# 代码分块数量通常在一万到两万之间。五百一十二维已足以保留语义方向，同时比
# 完整一千五百三十六维显著降低 JSONB 扫描时的内存和网络传输压力。
DEFAULT_REAL_EMBEDDING_DIMENSION = 512


class EmbeddingProviderError(RuntimeError):
    """真实 embedding 服务未配置、调用失败或返回结构无效时抛出的安全异常。"""


class EmbeddingProvider(Protocol):
    """可插拔 embedding 提供方契约。"""

    model_name: str
    dimension: int

    def embed(self, text: str) -> List[float]:
        """将一条文本转换为固定维度的归一化向量。

        参数:
            - `text`：待生成向量的文本，类型为 `str`。
        """

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        """批量转换文本并保持输入顺序。

        参数:
            - `texts`：同一批待向量化文本，类型为 `Iterable[str]`。
        """


def _normalized_tokens(text: str) -> List[str]:
    """提取确定性向量使用的英文标识符和中文词片段。

    参数:
        - `text`：需要生成离线特征的文本，类型为 `str`。

    Returns:
        保持原始顺序的英文符号和两字以上中文片段。
    """
    return re.findall(
        r"[A-Za-z_][A-Za-z0-9_:]*|[\u4e00-\u9fff]{2,}",
        text.casefold(),
    )


def _normalize_vector(vector: List[float]) -> List[float]:
    """将向量缩放为 L2 单位长度，零向量保持原样。

    参数:
        - `vector`：待归一化的浮点数组，类型为 `List[float]`。
    """
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


class DeterministicEmbeddingProvider:
    """基于哈希特征的离线 embedding 提供方，仅用于测试和功能降级。"""

    model_name = EMBEDDING_MODEL

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        """创建指定维度的离线向量提供方。

        参数:
            - `dimension`：向量维度，类型为 `int`。
        """
        if dimension < 8 or dimension > 2048:
            raise ValueError("向量维度必须在 8 到 2048 之间")
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        """生成稳定的哈希特征向量。

        参数:
            - `text`：待生成向量的文本，类型为 `str`。
        """
        vector = [0.0] * self.dimension
        for token in _normalized_tokens(str(text or "")):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        return _normalize_vector(vector)

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        """逐条生成离线向量并保持输入顺序。

        参数:
            - `texts`：同一批待向量化文本，类型为 `Iterable[str]`。
        """
        return [self.embed(text) for text in texts]


class OpenAICompatibleEmbeddingProvider:
    """调用 OpenAI-compatible `/embeddings` 接口生成真实语义向量。"""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str = DEFAULT_REAL_EMBEDDING_MODEL,
        dimension: int = DEFAULT_REAL_EMBEDDING_DIMENSION,
        batch_size: int = 8,
        concurrency: int = 2,
        max_retries: int = 3,
        timeout_seconds: float = 60.0,
        send_dimensions: bool = True,
    ) -> None:
        """初始化真实向量提供方。

        参数:
            - `endpoint`：完整 embedding HTTP(S) 地址，由运维配置或从 LLM 地址派生。
            - `api_key`：供应商鉴权密钥；仅放入请求头，不写入日志或异常消息。
            - `model`：供应商 embedding 模型标识，同时作为索引兼容判断依据。
            - `dimension`：期望向量维度，必须与索引和查询保持一致。
            - `batch_size`：单次 HTTP 请求最多提交的文本数。
            - `concurrency`：仓库批量索引时允许并行的请求批次数，用于控制全量
              索引耗时和供应商限流之间的平衡。
            - `max_retries`：超时、429 和 5xx 的最大重试次数；鉴权、模型不存在等
              不可恢复的 4xx 不重试。
            - `timeout_seconds`：单次批量 embedding 请求超时秒数。
            - `send_dimensions`：是否在请求体发送 `dimensions`；OpenAI 新模型
              支持该参数，部分兼容供应商固定维度时需要关闭。
        """
        endpoint = str(endpoint or "").strip()
        model = str(model or "").strip()
        if not endpoint.startswith(("https://", "http://")):
            raise EmbeddingProviderError("embedding 接口地址必须是 HTTP(S) URL")
        if not model:
            raise EmbeddingProviderError("embedding 模型不能为空")
        if dimension < 1 or dimension > 8192:
            raise EmbeddingProviderError("embedding 维度必须在 1 到 8192 之间")
        if batch_size < 1 or batch_size > 128:
            raise EmbeddingProviderError("embedding 批量大小必须在 1 到 128 之间")
        if concurrency < 1 or concurrency > 16:
            raise EmbeddingProviderError("embedding 并发数必须在 1 到 16 之间")
        if max_retries < 0 or max_retries > 10:
            raise EmbeddingProviderError("embedding 重试次数必须在 0 到 10 之间")
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise EmbeddingProviderError("embedding 超时必须在 0 到 300 秒之间")
        self.endpoint = endpoint
        self._api_key = str(api_key or "")
        self.model_name = model
        self.dimension = dimension
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.max_retries = max_retries
        self.timeout_seconds = float(timeout_seconds)
        self.send_dimensions = bool(send_dimensions)

    def embed(self, text: str) -> List[float]:
        """生成一条文本的真实语义向量。

        参数:
            - `text`：用户问题或源码分块文本，类型为 `str`；空文本返回零向量。

        Returns:
            与配置维度一致的单位向量。

        Raises:
            EmbeddingProviderError：网络失败、鉴权失败、HTTP 非 2xx、响应数量或维度
              不匹配时抛出；异常消息不包含密钥和响应正文。
        """
        return self.embed_many([text])[0] if str(text or "").strip() else [0.0] * self.dimension

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        """按受控批次调用 embedding 接口并还原输入顺序。

        参数:
            - `texts`：项目分块或查询文本，类型为 `Iterable[str]`。

        Returns:
            与输入等长且顺序一致的向量列表。
        """
        values = [str(text or "") for text in texts]
        if not values:
            return []
        vectors: List[List[float]] = []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                for offset in range(0, len(values), self.batch_size):
                    batch = values[offset:offset + self.batch_size]
                    payload: dict[str, Any] = {"model": self.model_name, "input": batch}
                    if self.dimension and self.send_dimensions:
                        payload["dimensions"] = self.dimension
                    response = self._post_with_retry(client, headers, payload)
                    if response.status_code < 200 or response.status_code >= 300:
                        raise EmbeddingProviderError(
                            f"embedding 接口返回 HTTP {response.status_code}"
                        )
                    vectors.extend(self._parse_batch(response.json(), len(batch)))
        except EmbeddingProviderError:
            raise
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError("embedding 响应不是合法 JSON") from exc
        return vectors

    def _post_with_retry(self, client: httpx.Client, headers: Dict[str, str], payload: Dict[str, Any]):
        """发送 embedding 请求，并只对瞬时网络错误、429 和 5xx 做退避重试。

        参数:
            - `client`：当前批次使用的 HTTP 客户端，超时由 provider 配置控制。
            - `headers`：鉴权与 JSON 请求头，不会写入日志。
            - `payload`：当前批次模型、文本和可选维度。

        Returns:
            成功或不可重试的 HTTP 响应。网络异常和 429/5xx 在重试耗尽后转换为
            `EmbeddingProviderError`，避免索引以部分成功状态结束。
        """
        for attempt in range(self.max_retries + 1):
            try:
                response = client.post(self.endpoint, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt >= self.max_retries:
                    if isinstance(exc, httpx.TimeoutException):
                        raise EmbeddingProviderError("embedding 请求超时") from exc
                    raise EmbeddingProviderError("embedding 网络请求失败") from exc
                time.sleep(min(8.0, 2.0 ** attempt))
                continue
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt >= self.max_retries:
                return response
            retry_after = str(response.headers.get("Retry-After", "") or "").strip()
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 2.0 ** attempt
            time.sleep(min(30.0, max(0.5, delay)))
        raise EmbeddingProviderError("embedding 请求重试状态异常")

    def _parse_batch(self, payload: Any, expected_count: int) -> List[List[float]]:
        """校验单个响应批次并按照 `index` 字段恢复顺序。

        参数:
            - `payload`：embedding 接口返回的 JSON 对象。
            - `expected_count`：本批次提交的文本数量。

        Returns:
            已校验维度的单位向量列表。
        """
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != expected_count:
            raise EmbeddingProviderError("embedding 响应数量与请求不一致")
        ordered: List[Optional[List[float]]] = [None] * expected_count
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise EmbeddingProviderError("embedding 响应条目格式无效")
            index = item.get("index", position)
            if not isinstance(index, int) or index < 0 or index >= expected_count:
                raise EmbeddingProviderError("embedding 响应索引越界")
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list) or len(raw_vector) != self.dimension:
                raise EmbeddingProviderError("embedding 向量维度与配置不一致")
            try:
                vector = [float(value) for value in raw_vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingProviderError("embedding 向量包含非法数值") from exc
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingProviderError("embedding 向量包含非有限数值")
            ordered[index] = _normalize_vector(vector)
        if any(vector is None for vector in ordered):
            raise EmbeddingProviderError("embedding 响应缺少向量条目")
        return [vector for vector in ordered if vector is not None]


def derive_embedding_endpoint(llm_api_url: str) -> str:
    """从已有 LLM 地址派生 OpenAI-compatible embedding 地址。

    参数:
        - `llm_api_url`：当前 LLM 的 Responses 或 Chat Completions 完整地址。

    Returns:
        以 `/embeddings` 结尾的地址；空输入返回空字符串。显式 embedding 地址
        始终优先于该派生值。
    """
    value = str(llm_api_url or "").strip().rstrip("/")
    if not value:
        return ""
    for suffix in ("/responses", "/chat/completions"):
        if value.endswith(suffix):
            return f"{value[:-len(suffix)]}/embeddings"
    return f"{value}/embeddings"


def get_configured_embedding_provider(settings: Any) -> EmbeddingProvider:
    """按应用设置返回真实或离线 embedding 提供方。

    参数:
        - `settings`：应用配置对象，类型为 `Any`；启用真实向量时需要 embedding
          模型、接口地址或可派生的 LLM 地址以及可选鉴权密钥。

    Returns:
        启用 `embedding_enabled` 时返回真实 provider，否则返回本地确定性 provider。
        真实 provider 配置错误会直接抛出，避免静默使用哈希向量冒充语义检索。
    """
    if not bool(getattr(settings, "embedding_enabled", False)):
        return DeterministicEmbeddingProvider()
    endpoint = str(getattr(settings, "embedding_api_url", "") or "").strip()
    if not endpoint:
        endpoint = derive_embedding_endpoint(str(getattr(settings, "llm_api_url", "") or ""))
    api_key = str(getattr(settings, "embedding_api_key", "") or "").strip()
    if not api_key:
        api_key = str(getattr(settings, "llm_api_key", "") or "").strip()
    return OpenAICompatibleEmbeddingProvider(
        endpoint=endpoint,
        api_key=api_key,
        model=str(
            getattr(settings, "embedding_model", DEFAULT_REAL_EMBEDDING_MODEL)
            or DEFAULT_REAL_EMBEDDING_MODEL
        ),
        dimension=int(
            getattr(settings, "embedding_dimension", DEFAULT_REAL_EMBEDDING_DIMENSION)
            or DEFAULT_REAL_EMBEDDING_DIMENSION
        ),
        batch_size=int(getattr(settings, "embedding_batch_size", 8) or 8),
        concurrency=int(getattr(settings, "embedding_concurrency", 2) or 2),
        max_retries=int(getattr(settings, "embedding_max_retries", 3)),
        timeout_seconds=float(getattr(settings, "embedding_timeout_seconds", 60.0) or 60.0),
        send_dimensions=bool(getattr(settings, "embedding_send_dimensions", True)),
    )


def embedding_index_version(provider: EmbeddingProvider) -> str:
    """生成包含向量模型和维度的索引版本，防止配置变化后复用旧向量。

    参数:
        - `provider`：当前用于查询和索引的 embedding provider。

    Returns:
        离线 provider 保持历史 `v1`；真实 provider 返回不超过 32 字符的稳定版本键。
    """
    if provider.model_name == EMBEDDING_MODEL:
        return "v1"
    identity = f"{provider.model_name}:{provider.dimension}".encode("utf-8")
    return f"v2-{hashlib.sha256(identity).hexdigest()[:16]}"


def get_embedding_provider(
    name: str = EMBEDDING_MODEL,
    dimension: int = EMBEDDING_DIMENSION,
) -> EmbeddingProvider:
    """按名称返回离线测试 provider，并拒绝未知模型名。

    参数:
        - `name`：provider 名称，类型为 `str`；当前工厂只返回离线 provider。
        - `dimension`：离线向量维度，类型为 `int`。
    """
    if name != EMBEDDING_MODEL:
        raise ValueError(f"暂不支持 embedding 提供方: {name}")
    return DeterministicEmbeddingProvider(dimension)


def embed_text(text: str, dimension: int = EMBEDDING_DIMENSION) -> List[float]:
    """使用离线哈希特征生成文本向量。

    参数:
        - `text`：待生成向量的文本，类型为 `str`。
        - `dimension`：离线向量维度，类型为 `int`。
    """
    return get_embedding_provider(dimension=dimension).embed(text)


def cosine_similarity(left: List[float], right: Optional[List[float]]) -> float:
    """计算同维向量余弦相似度；缺失或维度不一致时返回零。

    参数:
        - `left`：查询向量，类型为 `List[float]`。
        - `right`：分块向量，类型为 `Optional[List[float]]`。
    """
    if not right or len(left) != len(right):
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


__all__ = [
    "DEFAULT_REAL_EMBEDDING_DIMENSION",
    "DEFAULT_REAL_EMBEDDING_MODEL",
    "DeterministicEmbeddingProvider",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_MODEL",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "OpenAICompatibleEmbeddingProvider",
    "cosine_similarity",
    "derive_embedding_endpoint",
    "embed_text",
    "embedding_index_version",
    "get_configured_embedding_provider",
    "get_embedding_provider",
]
