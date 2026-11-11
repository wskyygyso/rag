"""Agentic RAG 重构前的跨模块行为契约测试。"""

import json
from pathlib import Path

import pytest

from app import worker
from app.business_analysis import build_deterministic_findings
from app.retrieval_planner import assess_retrieval_support


_BASELINE_PATH = Path(__file__).parent / "fixtures" / "refactor" / "contract-baseline.json"


def _load_baseline() -> dict:
    """读取重构前固定行为基线。

    Returns:
        `tests/fixtures/refactor/contract-baseline.json` 的字典结构。该文件只保存
        可观察契约，不保存业务答案到问句的映射。
    """
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _context(start_line: int, values: list[str]) -> str:
    """把源码片段转换为检索工具使用的带行号上下文。

    参数:
        - `start_line`：第一行对应的真实源码行号，类型为 `int`。
        - `values`：连续源码行文本，类型为 `list[str]`。

    Returns:
        每行带 `行号:` 前缀的上下文文本，供确定性分析器恢复稳定证据引用。
    """
    return "\n".join(f"{start_line + offset}: {value}" for offset, value in enumerate(values))


def test_worker_routes_and_retrieval_stage_contracts_are_stable() -> None:
    """验证任务队列、显式符号和检索阶段标记保持重构前语义。"""
    baseline = _load_baseline()["worker_contract"]

    assert worker.celery_app.conf.task_routes["app.worker.run_diagnosis_task"]["queue"] == baseline["diagnosis_queue"]
    assert worker.celery_app.conf.task_routes["app.worker.run_document_index_task"]["queue"] == baseline["index_queue"]
    assert worker._explicit_symbol_queries(
        ["FuseRecoveryService", "GuildController::inviteGuild", "Redis", "SQL"]
    ) == baseline["explicit_symbols"]
    assert worker._tag_initial_retrieval_stage(
        {"matched_queries": ["钻石提现"]}, ["钻石提现"], ["AdvanceServices"]
    ) == baseline["anchor_stage"]
    assert worker._tag_initial_retrieval_stage(
        {"matched_queries": ["AdvanceServices"]}, ["钻石提现"], ["AdvanceServices"]
    ) == baseline["symbol_stage"]


def test_retrieval_support_contract_distinguishes_business_and_generic_hits() -> None:
    """验证业务注册链路可继续扩展，而泛词共现必须停止扩展。"""
    baseline = _load_baseline()["retrieval_support"]
    grounded = assess_retrieval_support(
        [
            {
                "file": "service/php/model/usermodel.php",
                "retrieval_stage": "concept_expansion",
                "matched_concepts": ["device", "registration", "account"],
            }
        ],
        ["device", "registration", "account", "count"],
    )
    generic_only = assess_retrieval_support(
        [
            {
                "file": "service/php/model/usermodel.php",
                "retrieval_stage": "concept_expansion",
                "matched_concepts": ["device", "count"],
            }
        ],
        ["device", "registration", "account", "count"],
    )

    for key, value in baseline["device_registration"].items():
        assert grounded[key] == value
    for key, value in baseline["generic_only"].items():
        assert generic_only[key] == value


def test_device_registration_findings_contract_is_stable() -> None:
    """验证设备注册事实、配置值和源码引用在模块拆分后保持一致。"""
    baseline = _load_baseline()["device_registration"]
    result = {
        "evidence": [
            {
                "file": "service/php/config/device.php",
                "line": 4,
                "context": _context(
                    3,
                    [
                        "return [",
                        "    // 单个设备注册用户数限制",
                        "    'device_user_count_limit' => 3,",
                        "];",
                    ],
                ),
            },
            {
                "file": "service/php/model/usermodel.php",
                "line": 1712,
                "context": _context(
                    1707,
                    [
                        "public function checkRegCount($device_id = '') {",
                        "$sql = 'select reg_count from hsing_util.t_reg_divice where device_id = ?';",
                        "$regCount = intval($res[0]['reg_count']);",
                        "if ($regCount >= Config::get('device.device_user_count_limit')) {",
                        "    return false;",
                        "}",
                    ],
                ),
            },
            {
                "file": "service/php/api/sms_kit/sendCode.php",
                "line": 50,
                "context": _context(
                    47,
                    [
                        "$regCount = $umodel->getDeviceRegCount(strval($deviceId));",
                        "if ($regCount >= \\Conf\\Config::get('device.device_user_count_limit')) {",
                        "    return ['ret' => -10003];",
                        "}",
                    ],
                ),
            },
        ]
    }

    findings = build_deterministic_findings("同一设备号最多能注册多少个用户？", result)
    categories = {item["category"] for item in findings["facts"]}
    statements = " ".join(item["statement"] for item in findings["facts"])

    assert findings["conclusion"] == baseline["conclusion"]
    assert set(baseline["required_categories"]) <= categories
    assert baseline["config_statement_fragment"] in statements
    assert any(ref.endswith(baseline["evidence_ref_suffix"]) for ref in findings["evidence_refs"])


def test_negative_device_question_never_invents_numeric_limit() -> None:
    """验证缺少配置定义时设备注册问题保持零事实，防止重构扩大推断边界。"""
    result = {
        "evidence": [
            {
                "file": "service/php/model/usermodel.php",
                "line": 1712,
                "context": _context(
                    1710,
                    ["if ($regCount >= $deviceLimit) {", "    return false;", "}"],
                ),
            }
        ]
    }

    findings = build_deterministic_findings("同一设备号最多能注册多少个用户？", result)

    assert findings["facts"] == []
    assert "3" not in findings["conclusion"]
