"""生产上线预检测试。"""

import json

import pytest

from app.preflight import enforce_production_settings, static_preflight
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
        database_url="postgresql://metadata",
        validation_database_url="",
        code_repositories_root=str(repository_root),
    )
    results = {result.name: result for result in static_preflight(settings, strict=True)}
    assert results["authentication"].status == "FAIL"
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
        database_url="postgresql://same",
        validation_database_url="postgresql://same",
        code_repositories_root=str(repository_root),
    )
    results = {result.name: result for result in static_preflight(settings, strict=False)}
    assert results["validation_database_url"].status == "FAIL"


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
        database_url="postgresql://metadata",
        validation_database_url="postgresql://readonly",
        code_repositories_root=str(repository_root),
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
        database_url="postgresql://metadata",
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
        database_url="postgresql://metadata",
        validation_database_url="postgresql://readonly",
        code_repositories_root=str(repository_root),
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
