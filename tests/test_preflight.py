"""生产上线预检测试。"""

import json

from app.preflight import static_preflight
from app.settings import Settings


def test_strict_preflight_rejects_missing_production_controls(tmp_path) -> None:
    """验证严格模式拒绝关闭认证和缺少目标只读库。"""
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
    """验证验证数据库与元数据库地址相同时直接失败。"""
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
    """验证有效认证、独立数据库和超时配置通过静态检查。"""
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
