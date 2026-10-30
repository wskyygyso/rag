"""应用配置模型和配置加载入口。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量和 `.env` 文件读取的应用运行配置。

    字段名会按 Pydantic Settings 规则转换为大写环境变量名；生产环境敏感字段
    必须由 Secret Manager 注入，不能写入版本库。
    """

    # 应用运行环境和 HTTP 监听信息。
    app_env: str = "development"
    app_name: str = "php-diagnostic-ragent"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # 被诊断代码仓库的白名单根目录，运行时应以只读方式挂载。
    code_repositories_root: str = "/repositories"

    # 验证接口 Redis 固定窗口限流：分钟请求上限和窗口秒数。
    validation_rate_limit_per_minute: int = 60
    validation_rate_limit_window_seconds: int = 60

    # 诊断任务软超时先写入失败原因，硬超时由 Celery 强制结束进程。
    diagnosis_task_soft_time_limit_seconds: int = 240
    diagnosis_task_time_limit_seconds: int = 300

    # 文档索引任务使用独立软/硬超时，避免长时间索引阻塞诊断任务。
    document_index_soft_time_limit_seconds: int = 900
    document_index_time_limit_seconds: int = 960

    # 历史数据保留天数，由 cleanup_retention 脚本按批次清理。
    diagnostic_task_retention_days: int = 90
    validation_audit_retention_days: int = 180
    security_audit_retention_days: int = 180
    document_index_retention_days: int = 90

    # API Key 认证开关；生产环境由启动门禁强制要求开启。
    auth_enabled: bool = False

    # API Key JSON：调用方主体、项目白名单和 read/write scope。
    api_keys_json: str = "{}"

    # 写请求体大小上限，超限在认证和 JSON 解析前返回 413。
    max_request_body_bytes: int = 1_000_000

    # 逗号分隔的跨域来源白名单；空字符串表示不开放跨域。
    cors_allowed_origins: str = ""

    # LLM 诊断默认关闭；启用后由 Worker 生成证据约束的诊断叙写。
    llm_enabled: bool = False

    # LLM 请求地址。URL 以 /responses 结尾时使用 Responses API，否则使用 Chat Completions。
    llm_api_url: str = "https://api.openai.com/v1/responses"

    # auto 根据 URL 自动选择协议，也可显式配置 responses 或 chat_completions。
    llm_api_protocol: str = "auto"

    # LLM 鉴权密钥，生产环境必须由 Secret Manager 注入。
    llm_api_key: str = ""

    # 供应商模型标识或 Endpoint ID。
    llm_model: str = ""

    # 单次 LLM 网络调用超时秒数。
    llm_timeout_seconds: float = 30.0

    # 调用 LLM 前要求的最小去重证据数，避免零证据付费调用。
    llm_min_evidence_items: int = 1

    # LLM 结果缓存开关和 Redis TTL 秒数。
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 3600

    # RAgent 自身 Redis：健康检查、验证限流和 LLM 缓存。
    redis_url: str = "redis://localhost:6379/0"

    # RAgent 元数据库，仅保存任务、证据、反馈、索引和审计。
    database_url: str = "postgresql://ragent:ragent@localhost:5432/ragent"

    # 目标业务库只读地址，不能与元数据库复用；留空时数据库验证不可用。
    validation_database_url: str = ""

    # Celery broker 和结果后端使用独立 Redis DB，避免键空间冲突。
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Pydantic Settings 从 .env 读取，未知环境变量不会导致启动失败。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置实例，避免重复解析环境变量。"""
    return Settings()
