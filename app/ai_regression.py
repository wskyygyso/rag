"""AI 固定评测运行的趋势和回归阈值判断。"""

from typing import Any, Dict, Iterable, List, Optional


def evaluate_ai_regression(
    runs: Iterable[Dict[str, Any]],
    *,
    min_pass_rate: float = 1.0,
    min_evidence_recall: float = 1.0,
    max_pass_rate_drop: float = 0.1,
) -> Dict[str, Any]:
    """判断最新 AI 评测是否达到绝对阈值和相对历史趋势。

    参数:
        - `runs`：按时间倒序排列的 AI 评测运行记录。
        - `min_pass_rate`：最新运行要求的最低用例通过率。
        - `min_evidence_recall`：最新运行要求的最低平均证据引用召回。
        - `max_pass_rate_drop`：相对上一次运行允许的最大通过率下降值。
    """
    items: List[Dict[str, Any]] = list(runs)
    if not items:
        return {
            "passed": False,
            "reason": "没有可用的 AI 评测运行记录",
            "latest": None,
            "previous": None,
        }
    if not 0 <= min_pass_rate <= 1 or not 0 <= min_evidence_recall <= 1:
        raise ValueError("最低通过率和证据召回必须位于 0 到 1 之间")
    if max_pass_rate_drop < 0 or max_pass_rate_drop > 1:
        raise ValueError("最大通过率下降阈值必须位于 0 到 1 之间")

    latest = items[0]
    previous: Optional[Dict[str, Any]] = items[1] if len(items) > 1 else None
    latest_pass = float(latest.get("pass_rate", 0.0))
    latest_recall = float(latest.get("average_evidence_ref_recall", 0.0))
    drop = latest_pass - float(previous.get("pass_rate", 0.0)) if previous else 0.0
    checks = {
        "pass_rate": latest_pass >= min_pass_rate,
        "evidence_recall": latest_recall >= min_evidence_recall,
        "trend": drop >= -max_pass_rate_drop,
    }
    reasons = []
    if not checks["pass_rate"]:
        reasons.append("最新通过率低于阈值")
    if not checks["evidence_recall"]:
        reasons.append("最新证据引用召回低于阈值")
    if not checks["trend"]:
        reasons.append("通过率相对上一次下降超过允许值")
    return {
        "passed": all(checks.values()),
        "reason": "通过" if all(checks.values()) else "；".join(reasons),
        "checks": checks,
        "latest": latest,
        "previous": previous,
        "pass_rate_delta": round(drop, 6),
        "thresholds": {
            "min_pass_rate": min_pass_rate,
            "min_evidence_recall": min_evidence_recall,
            "max_pass_rate_drop": max_pass_rate_drop,
        },
    }
