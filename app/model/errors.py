"""模型配置、响应校验和供应商调用错误。"""


class LLMConfigurationError(ValueError):
    """LLM 配置不完整、相互冲突或不满足安全预算时抛出的异常。"""


class LLMResponseError(ValueError):
    """模型响应为空、无法解析或引用不存在证据时抛出的异常。"""


class ModelGatewayError(LLMResponseError):
    """表示全部候选失败，并携带不含密钥的尝试分类摘要。"""

    def __init__(self, message: str, metadata: dict) -> None:
        """创建包含安全降级元数据的网关异常。

        参数:
            - `message`：面向调用方的受控失败摘要。
            - `metadata`：候选尝试、错误分类和次数等不含密钥的诊断元数据。
        """
        super().__init__(message)
        self.metadata = dict(metadata)


class ModelInvocationError(Exception):
    """表示一次供应商调用失败及其是否适合切换候选模型。

    `safe_message` 只保存响应状态、错误类型等可审计摘要，不保存请求头、
    API Key、完整模型输出或用户证据正文。
    """

    def __init__(
        self,
        *,
        error_type: str,
        safe_message: str,
        retryable: bool,
        status_code: int = 0,
    ) -> None:
        """创建模型调用错误。

        参数:
            - `error_type`：稳定的错误分类，用于指标和熔断统计。
            - `safe_message`：可写入任务元数据的短摘要，不得含密钥或完整请求体。
            - `retryable`：是否允许切换下一个模型候选。
            - `status_code`：供应商 HTTP 状态码；网络或响应格式错误时为 0。
        """
        super().__init__(safe_message)
        self.error_type = error_type
        self.safe_message = safe_message
        self.retryable = retryable
        self.status_code = status_code
