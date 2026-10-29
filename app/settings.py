"""应用配置模型和配置加载入口。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量和 `.env` 文件读取的应用运行配置。"""

    app_env: str = "development"
    app_name: str = "php-diagnostic-ragent"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    code_repositories_root: str = "/repositories"
    validation_rate_limit_per_minute: int = 60
    validation_rate_limit_window_seconds: int = 60
    diagnosis_task_soft_time_limit_seconds: int = 240
    diagnosis_task_time_limit_seconds: int = 300
    document_index_soft_time_limit_seconds: int = 900
    document_index_time_limit_seconds: int = 960
    diagnostic_task_retention_days: int = 90
    validation_audit_retention_days: int = 180
    security_audit_retention_days: int = 180
    document_index_retention_days: int = 90
    auth_enabled: bool = False
    api_keys_json: str = "{}"
    max_request_body_bytes: int = 1_000_000
    cors_allowed_origins: str = ""
    llm_enabled: bool = False
    llm_api_url: str = "https://api.openai.com/v1/responses"
    llm_api_protocol: str = "auto"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0
    llm_min_evidence_items: int = 1

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://ragent:ragent@localhost:5432/ragent"
    validation_database_url: str = ""
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置实例，避免重复解析环境变量。"""
    return Settings()
