"""生产镜像、依赖锁、编排和发布模板资产测试。"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_dependencies_are_pinned_and_image_runs_non_root() -> None:
    """验证运行时依赖全部固定版本且镜像使用固定 UID 非 root 运行。"""
    lines = [
        line.strip()
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines
    assert all("==" in line for line in lines)
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "sha256:" in dockerfile.splitlines()[2]
    assert "python -m pip check" in dockerfile
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "redis>=6.4,<9.0" in pyproject
    assert "celery[redis]>=5.7,<6.0" in pyproject


def test_production_compose_hardens_all_application_services() -> None:
    """验证生产编排只运行应用进程并启用只读根文件系统和权限收敛。"""
    path = ROOT / "docker-compose.production.yml"
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"api", "worker", "index-worker"}
    for service in compose["services"].values():
        assert service["read_only"] is True
        assert "ALL" in service["cap_drop"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert ":ro" in service["volumes"][0]
        assert service["healthcheck"]["test"][0] == "CMD-SHELL" or service["healthcheck"]["test"][0] == "CMD"

    for name in ("worker", "index-worker"):
        command = compose["services"][name]["healthcheck"]["test"][1]
        assert "inspect ping" in command
        assert "celery@$$HOSTNAME" in command


def test_local_workers_have_queue_specific_healthchecks() -> None:
    """验证本地诊断和索引 Worker 都能独立暴露 Celery 健康状态。"""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name in ("worker", "index-worker"):
        command = compose["services"][name]["healthcheck"]["test"][1]
        assert "inspect ping" in command
        assert "celery@$$HOSTNAME" in command


def test_release_workflow_is_read_only_and_blocks_high_vulnerabilities() -> None:
    """验证标签发布只读取仓库，并保留 SBOM 与高危漏洞阻断步骤。"""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["release"]["steps"]
    step_text = "\n".join(f"{step.get('name', '')}\n{step.get('uses', '')}" for step in steps)
    assert "anchore/sbom-action" in step_text
    assert "aquasecurity/trivy-action" in step_text
    trivy_step = next(step for step in steps if step.get("uses", "").startswith("aquasecurity/trivy-action"))
    assert trivy_step["with"]["exit-code"] == "1"
    assert trivy_step["with"]["severity"] == "CRITICAL,HIGH"


def test_ci_builds_locked_image_and_checks_migration_entrypoint() -> None:
    """验证 CI 同时执行源码质量门禁和锁定依赖生产镜像入口检查。"""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"] + workflow["jobs"]["image"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "pytest -q" in commands
    assert "python scripts/check_comment_coverage.py" in commands
    assert "docker run --rm ragent:ci id -u" in commands
    assert "scripts/migrate_database.py --help" in commands


def test_production_template_has_no_real_secret_and_defines_release_image() -> None:
    """验证生产模板保留占位密钥并要求不可变镜像与只读仓库路径。"""
    template = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert "RAGENT_IMAGE=" in template
    assert "RAGENT_REPOSITORIES_ROOT=" in template
    assert "<DATABASE_PASSWORD>" in template
    assert "<READONLY_PASSWORD>" in template
    assert "<GENERATE_RANDOM_API_KEY>" in template
