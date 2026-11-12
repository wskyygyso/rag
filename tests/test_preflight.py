"""生产上线预检测试。"""

import json

import pytest

from app.preflight import check_embedding_service, enforce_production_settings, static_preflight
from app.settings import Settings


def test_strict_preflight_rejects_missing_production_controls(tmp_path) -> None:
    """验证严格模式拒绝关闭认证和缺少目标只读库。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    settings = Settings(
        app_env="production",
        auth_enabled=False,
        # 显式关闭 embedding，避免本机 `.env` 中的真实向量配置让严格门禁
        # 误判为 PASS，从而掩盖“生产缺少必需控制项”这一被测行为。
        embedding_enabled=False,
        database_url="postgresql://metadata/ragent",
        validation_database_url="",
        code_repositories_root=str(repository_root),
    )
    results = {result.name: result for result in static_preflight(settings, strict=True)}
    assert results["authentication"].status == "FAIL"
    assert results["embedding"].status == "FAIL"
    assert results["validation_database_url"].status == "FAIL"
    assert results["metadata_database_url"].status == "PASS"


def test_preflight_rejects_reusing_metadata_database(tmp_path) -> None:
    """验证验证数据库与元数据库地址相同时直接失败。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    settings = Settings(
        database_url="postgresql://same/ragent",
        validation_database_url="postgresql://different-user:secret@SAME:5432/ragent?application_name=validation",
        code_repositories_root=str(repository_root),
    )
    results = {result.name: result for result in static_preflight(settings, strict=False)}
    assert results["validation_database_url"].status == "FAIL"


def test_strict_preflight_rejects_placeholder_secrets_and_wildcard_cors(tmp_path) -> None:
    """验证严格门禁拒绝模板占位凭据和通配 CORS。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    api_key = "preflight-placeholder-test-key"
    settings = Settings(
        app_env="production",
        auth_enabled=True,
        api_keys_json=json.dumps(
            {api_key: {"subject": "ops", "projects": ["waky3"], "scopes": ["read", "write"]}}
        ),
        database_url="postgresql://ragent:<DATABASE_PASSWORD>@postgres/ragent",
        validation_database_url="postgresql://readonly:<READONLY_PASSWORD>@target/business",
        code_repositories_root=str(repository_root),
        cors_allowed_origins="*",
    )
    results = {result.name: result for result in static_preflight(settings, strict=True)}
    assert results["secret_placeholders"].status == "FAIL"
    assert results["cors"].status == "FAIL"


def test_preflight_accepts_valid_static_configuration(tmp_path) -> None:
    """验证有效认证、独立数据库和超时配置通过静态检查。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    api_key = "preflight-test-key-with-enough-length"
    settings = Settings(
        app_env="production",
        auth_enabled=True,
        api_keys_json=json.dumps(
            {api_key: {"subject": "ops", "projects": ["waky3"], "scopes": ["read", "write"]}}
        ),
        database_url="postgresql://metadata/ragent",
        validation_database_url="postgresql://readonly/business",
        code_repositories_root=str(repository_root),
        embedding_enabled=True,
        embedding_api_url="https://api.example.com/v1/embeddings",
        embedding_model="multilingual-semantic-v2",
        embedding_dimension=1024,
    )
    results = static_preflight(settings, strict=True)
    assert all(result.status == "PASS" for result in results)


def test_production_startup_gate_rejects_incomplete_configuration(tmp_path) -> None:
    """验证生产启动门禁在认证或目标库缺失时拒绝启动。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    settings = Settings(
        app_env="production",
        auth_enabled=False,
        database_url="postgresql://metadata/ragent",
        validation_database_url="",
        code_repositories_root=str(repository_root),
    )
    with pytest.raises(ValueError, match="authentication"):
        enforce_production_settings(settings)


def test_production_startup_gate_accepts_complete_configuration(tmp_path) -> None:
    """验证完整生产静态配置可以通过启动门禁。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    api_key = "production-startup-test-key-length"
    settings = Settings(
        app_env="production",
        auth_enabled=True,
        api_keys_json=json.dumps(
            {api_key: {"subject": "ops", "projects": ["waky3"], "scopes": ["read", "write"]}}
        ),
        database_url="postgresql://metadata/ragent",
        validation_database_url="postgresql://readonly/business",
        code_repositories_root=str(repository_root),
        embedding_enabled=True,
        embedding_api_url="https://api.example.com/v1/embeddings",
        embedding_model="multilingual-semantic-v2",
        embedding_dimension=1024,
    )
    enforce_production_settings(settings)


def test_development_startup_does_not_require_production_controls(tmp_path) -> None:
    """验证开发环境保留本地调试兼容，不触发生产启动门禁。

    参数:
        - `tmp_path`：pytest 临时目录。
    """
    settings = Settings(
        app_env="development",
        auth_enabled=False,
        validation_database_url="",
        code_repositories_root=str(tmp_path / "missing"),
    )
    enforce_production_settings(settings)


@pytest.mark.asyncio
async def test_embedding_service_probe_checks_real_provider(tmp_path, monkeypatch) -> None:
    """验证运行期预检会实际调用 embedding，而不是只检查配置字符串。

    参数:
        - `tmp_path`：pytest 临时目录。
        - `monkeypatch`：pytest 运行期替换工具。
    """
    class Provider:
        """返回固定两维向量的真实 provider 测试替身。"""

        model_name = "multilingual-semantic-v2"
        dimension = 2

        def embed(self, text):
            """返回探测向量并确认探测文本不包含用户问题。

            参数:
                - `text`：预检固定探测文本。
            """
            assert text.startswith("RAgent semantic")
            return [1.0, 0.0]

    settings = Settings(
        embedding_enabled=True,
        embedding_api_url="https://api.example.com/v1/embeddings",
        embedding_model="multilingual-semantic-v2",
        embedding_dimension=2,
        code_repositories_root=str(tmp_path),
    )
    monkeypatch.setattr(
        "app.preflight.get_configured_embedding_provider",
        lambda current_settings: Provider(),
    )

    result = await check_embedding_service(settings)

    assert result is not None
    assert result.status == "PASS"
    assert result.detail == {"model": "multilingual-semantic-v2", "dimension": 2}


@pytest.mark.asyncio
async def test_embedding_service_probe_is_skipped_when_disabled() -> None:
    """验证未启用真实向量时不在运行期预检重复发起外部请求。"""
    settings = Settings(embedding_enabled=False)

    assert await check_embedding_service(settings) is None
