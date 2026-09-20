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

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://ragent:ragent@localhost:5432/ragent"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置实例，避免重复解析环境变量。"""
    return Settings()
