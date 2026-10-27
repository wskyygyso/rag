"""Prometheus 告警规则资产测试。"""

from pathlib import Path

import yaml


def test_prometheus_rules_cover_critical_failure_modes() -> None:
    """验证告警规则可解析并覆盖关键上线故障。"""
    path = Path(__file__).resolve().parents[1] / "monitoring" / "prometheus-rules.yml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = content["groups"][0]["rules"]
    names = {rule["alert"] for rule in rules}
    assert len(rules) == 8
    assert {
        "RagentDatabaseMetricsDown",
        "RagentHttpErrorRateHigh",
        "RagentHttpP95LatencyHigh",
        "RagentSecurityDenialsSpike",
        "RagentTaskRetrySpike",
    } <= names
    assert all(rule.get("expr") for rule in rules)
    assert all(rule["labels"]["severity"] in {"warning", "critical"} for rule in rules)
