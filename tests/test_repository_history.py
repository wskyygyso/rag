"""受控 Git 历史检索与版本事实分析测试。"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.business_analysis import build_deterministic_findings
from app.diagnosis_workflow import _history_version_parser_evidence
from app.repository_history import (
    history_search_terms,
    is_history_question,
    search_repository_history,
)


def _git(repository: Path, *arguments: str) -> str:
    """在临时仓库中执行测试所需的 Git 命令。

    参数:
        - `repository`：测试创建的临时 Git 仓库目录，类型为 `Path`。
        - `arguments`：固定测试 Git 参数，例如 `init`、`add` 和 `commit`。

    Returns:
        Git 标准输出文本；命令失败时直接抛出异常，防止测试在仓库未正确建立时
        仍误判历史检索通过。
    """
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def test_history_question_terms_drop_timeline_shell_and_cross_boundary_noise() -> None:
    """验证“哪个版本开始”只生成领域词，不把版本提问外壳送进 Git。"""
    question = "waky3 多开设置是那个版本开始有的？"

    terms = history_search_terms(question, ["多开设置", "那个版本开始有", "多开"])
    alternative_question = "waky3 哪些 Android 客户端版本会启用多开校验？"
    alternative_terms = history_search_terms(
        alternative_question,
        ["Android", "客户端", "版本", "多开校验"],
    )

    assert is_history_question(question) is True
    assert is_history_question(alternative_question) is True
    assert "多开" in terms
    assert "多开设置" in terms
    assert "多开校验" in alternative_terms
    assert "校验" not in alternative_terms
    assert "设置" not in terms
    assert all("哪些" not in term for term in alternative_terms)
    assert "那个版本开始有" not in terms
    assert "个版" not in terms
    assert "本开" not in terms


@pytest.mark.skipif(shutil.which("git") is None, reason="测试环境没有 git")
def test_git_history_returns_first_relevant_commit_and_version_snapshot(tmp_path: Path) -> None:
    """验证嵌套子仓库中的首次相关提交、源码快照和版本常量可被稳定取证。

    参数:
        - `tmp_path`：pytest 为当前用例创建的独立临时目录，用于隔离临时 Git
          仓库并避免测试历史污染真实项目。
    """
    root = tmp_path / "repos"
    project = root / "demo"
    repository = project / "service"
    repository.mkdir(parents=True)
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    source = repository / "MultiOpenGuard.php"
    source.write_text(
        "<?php\nclass MultiOpenGuard\n{\n"
        "    const MIN_VERSION = 204;\n"
        "    const MIN_BUILD = 111;\n"
        "    // 多开注册校验\n}\n",
        encoding="utf-8",
    )
    _git(repository, "add", "MultiOpenGuard.php")
    _git(repository, "commit", "-m", "多开限制")
    first_commit = _git(repository, "rev-parse", "HEAD").strip()

    result = search_repository_history(str(root), "demo", ["多开"])

    assert result["warnings"] == []
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["commit"] == first_commit
    assert event["subject"] == "多开限制"
    assert any(
        "MIN_VERSION = 204" in item["content"]
        for item in event["snapshot_lines"]
    )


def test_history_facts_derive_version_from_commit_snapshot_without_project_rule() -> None:
    """验证历史分析器只根据快照常量和通用版本编码证据形成起始版本结论。"""
    history_events = [
        {
            "repository": "service",
            "commit": "abcdef1234567890",
            "short_commit": "abcdef123456",
            "authored_at": "2026-09-08T16:04:03+08:00",
            "subject": "多开限制",
            "files": ["service/MultiOpenGuard.php"],
            "snapshot_lines": [
                {
                    "file": "service/MultiOpenGuard.php",
                    "line": 4,
                    "content": "const MIN_VERSION = 204;",
                    "is_match": False,
                },
                {
                    "file": "service/MultiOpenGuard.php",
                    "line": 5,
                    "content": "const MIN_BUILD = 111;",
                    "is_match": False,
                },
            ],
        }
    ]
    result = {
        "history_events": history_events,
        "evidence": [
            {
                "file": "php/conf/version.php",
                "line": 642,
                "context": "642: $result['version'] = intval($parts[0] . $parts[1]);",
            }
        ],
        "retrieval_plan": {
            "retrieval_mode": "insufficient_support",
            "retrieval_support": {"grounded": False},
        },
    }

    findings = build_deterministic_findings("多开设置是哪个版本开始有的？", result)

    assert any(item["category"] == "版本门槛" for item in findings["facts"])
    assert any(item["category"] == "版本编码规则" for item in findings["facts"])
    assert "2.04.111" in findings["conclusion"]
    assert "不等同于线上商店首发日期" in findings["conclusion"]


def test_history_version_parser_evidence_reads_required_function_body(tmp_path: Path) -> None:
    """验证历史快照带回的解析函数名能够定位当前源码中的完整版本解码规则。

    参数:
        - `tmp_path`：pytest 为当前用例创建的隔离项目目录，用于构造一个不包含
          真实项目名称或完整业务问句的最小 PHP 仓库。
    """
    root = tmp_path / "repos"
    source = root / "demo" / "php" / "conf" / "client.php"
    source.parent.mkdir(parents=True)
    source.write_text(
        "<?php\n"
        "final class ClientProfile\n"
        "{\n"
        "    public static function getAppVersion(string $userAgent): array\n"
        "    {\n"
        "        $parts = explode('.', $userAgent);\n"
        "        return [\n"
        "            'version' => intval($parts[0] . $parts[1]),\n"
        "            'build' => intval($parts[2]),\n"
        "        ];\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    evidence = _history_version_parser_evidence(
        str(root),
        "demo",
        ["MIN_VERSION", "MIN_BUILD", "getAppVersion"],
    )

    assert len(evidence) == 1
    assert evidence[0]["type"] == "version_parser_code"
    assert evidence[0]["file"] == "php/conf/client.php"
    assert "explode('.', $userAgent)" in evidence[0]["context"]
    assert "intval($parts[0] . $parts[1])" in evidence[0]["context"]
