"""OpenAI-compatible 真实 embedding 提供方测试。"""

from types import SimpleNamespace

import httpx
import pytest

from app import embeddings
from app.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingProvider,
    derive_embedding_endpoint,
    embedding_index_version,
    get_configured_embedding_provider,
)


class _Response:
    """模拟 embedding HTTP 响应，只暴露提供方读取的状态码和 JSON。"""

    def __init__(self, status_code=200, payload=None, headers=None):
        """创建可控 HTTP 响应。

        参数:
            - `status_code`：HTTP 状态码，类型为 `int`。
            - `payload`：响应 JSON 对象，类型为 `dict | None`。
            - `headers`：需要模拟的重试响应头，类型为 `dict | None`。
        """
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        """返回预置 JSON 对象，避免测试发起真实网络请求。"""
        return self._payload


class _Client:
    """模拟 httpx.Client 的上下文管理器和 POST 行为。"""

    response = _Response()
    responses = []
    error = None
    calls = []

    def __init__(self, timeout):
        """记录提供方设置的网络超时。

        参数:
            - `timeout`：单次 HTTP 请求超时秒数。
        """
        self.timeout = timeout

    def __enter__(self):
        """进入客户端上下文并返回自身。"""
        return self

    def __exit__(self, exc_type, exc, traceback):
        """退出客户端上下文，测试无需释放真实连接。

        参数:
            - `exc_type`：上下文抛出的异常类型；无异常时为空。
            - `exc`：上下文抛出的异常实例；无异常时为空。
            - `traceback`：异常回溯对象；无异常时为空。
        """
        return False

    def post(self, url, headers, json):
        """记录请求并返回预置响应或抛出预置网络异常。

        参数:
            - `url`：embedding 接口完整地址。
            - `headers`：请求头，包括不能泄露到异常中的鉴权信息。
            - `json`：OpenAI-compatible embedding 请求体。
        """
        self.__class__.calls.append({"url": url, "headers": headers, "json": json})
        if self.__class__.error is not None:
            raise self.__class__.error
        if self.__class__.responses:
            return self.__class__.responses.pop(0)
        return self.__class__.response


def _patch_client(monkeypatch):
    """把提供方使用的 httpx.Client 替换为进程内测试客户端。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _Client.calls = []
    _Client.responses = []
    _Client.error = None
    monkeypatch.setattr(embeddings.httpx, "Client", _Client)


def test_embedding_endpoint_derivation_handles_responses_and_chat_urls() -> None:
    """验证不同 LLM 协议地址都能派生出 OpenAI-compatible embedding 地址。"""
    assert derive_embedding_endpoint("https://api.example.com/v1/responses") == "https://api.example.com/v1/embeddings"
    assert derive_embedding_endpoint("https://api.example.com/v1/chat/completions/") == "https://api.example.com/v1/embeddings"
    assert derive_embedding_endpoint("https://api.example.com/v1") == "https://api.example.com/v1/embeddings"
    assert derive_embedding_endpoint("") == ""


def test_real_embedding_restores_response_order(monkeypatch) -> None:
    """验证供应商返回乱序索引时，向量仍与输入文本一一对应。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _patch_client(monkeypatch)
    _Client.response = _Response(
        200,
        {
            "data": [
                {"index": 1, "embedding": [0.0, 2.0]},
                {"index": 0, "embedding": [3.0, 4.0]},
            ]
        },
    )
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.com/v1/embeddings",
        api_key="secret-key",
        model="semantic-test",
        dimension=2,
        max_retries=0,
    )

    vectors = provider.embed_many(["device registration limit", "账号注册上限"])

    assert vectors == [[0.6, 0.8], [0.0, 1.0]]
    assert _Client.calls[0]["json"]["model"] == "semantic-test"
    assert _Client.calls[0]["json"]["dimensions"] == 2
    assert _Client.calls[0]["headers"]["Authorization"] == "Bearer secret-key"


def test_real_embedding_rejects_dimension_mismatch(monkeypatch) -> None:
    """验证供应商维度与索引配置不一致时拒绝写入错误向量。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _patch_client(monkeypatch)
    _Client.response = _Response(200, {"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]})
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.com/v1/embeddings",
        api_key="",
        model="semantic-test",
        dimension=2,
    )

    with pytest.raises(EmbeddingProviderError, match="维度"):
        provider.embed("dimension check")


def test_real_embedding_rejects_non_finite_values(monkeypatch) -> None:
    """验证 NaN 或无穷值不会进入索引和余弦计算。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _patch_client(monkeypatch)
    _Client.response = _Response(
        200,
        {"data": [{"index": 0, "embedding": [1.0, float("nan")]}]},
    )
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.com/v1/embeddings",
        api_key="",
        model="semantic-test",
        dimension=2,
    )

    with pytest.raises(EmbeddingProviderError, match="非有限"):
        provider.embed("finite check")


def test_real_embedding_errors_do_not_expose_secret_or_response_body(monkeypatch) -> None:
    """验证鉴权失败只暴露受控错误，不把密钥或供应商正文写进异常。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _patch_client(monkeypatch)
    _Client.response = _Response(401, {"error": "secret response body"})
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.com/v1/embeddings",
        api_key="secret-key",
        model="semantic-test",
        dimension=2,
    )

    with pytest.raises(EmbeddingProviderError) as error:
        provider.embed("auth failure")

    message = str(error.value)
    assert "HTTP 401" in message
    assert "secret-key" not in message
    assert "secret response body" not in message


def test_real_embedding_retries_rate_limit_and_preserves_order(monkeypatch) -> None:
    """验证 429 会按受控退避重试，成功响应仍按 index 恢复输入顺序。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _patch_client(monkeypatch)
    _Client.responses = [
        _Response(429, headers={"Retry-After": "1"}),
        _Response(
            200,
            {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        ),
    ]
    monkeypatch.setattr(embeddings.time, "sleep", lambda seconds: None)
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.com/v1/embeddings",
        api_key="secret-key",
        model="semantic-test",
        dimension=2,
        max_retries=1,
    )

    vectors = provider.embed_many(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert len(_Client.calls) == 2


def test_real_embedding_timeout_is_classified(monkeypatch) -> None:
    """验证网络超时转换为稳定的 embedding 业务异常。

    参数:
        - `monkeypatch`：pytest 运行期替换工具。
    """
    _patch_client(monkeypatch)
    _Client.error = httpx.ReadTimeout("slow")
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.com/v1/embeddings",
        api_key="",
        model="semantic-test",
        dimension=2,
        max_retries=0,
    )

    with pytest.raises(EmbeddingProviderError, match="超时"):
        provider.embed("timeout")


def test_configured_provider_and_index_version_track_real_model() -> None:
    """验证开启真实向量后选择远程 provider，并生成独立的索引版本。"""
    settings = SimpleNamespace(
        embedding_enabled=True,
        embedding_api_url="",
        embedding_api_key="",
        embedding_model="multilingual-semantic-v2",
        embedding_dimension=1024,
        embedding_batch_size=16,
        embedding_concurrency=3,
        embedding_timeout_seconds=30,
        llm_api_url="https://api.example.com/v1/chat/completions",
        llm_api_key="shared-secret",
    )

    provider = get_configured_embedding_provider(settings)

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.endpoint == "https://api.example.com/v1/embeddings"
    assert provider._api_key == "shared-secret"
    assert embedding_index_version(provider).startswith("v2-")
    assert embedding_index_version(provider) != embedding_index_version(DeterministicEmbeddingProvider())
