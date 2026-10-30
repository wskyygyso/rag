"""AI 诊断调用成本、缓存和反馈质量聚合。"""

from collections import Counter
from typing import Any, Dict, Iterable


def _number(value: Any, default: float = 0.0) -> float:
    """将数据库中的数字或数字字符串安全转换为浮点数。

    参数:
        - `value`：数据库元数据中的原始数值。
        - `default`：值缺失或格式错误时使用的默认数字。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_ai_quality(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合 AI 调用元数据和人工反馈质量。

    参数:
        - `rows`：诊断任务结果及正确/不准确反馈计数的数据库行集合。
    """
    models = Counter()
    protocols = Counter()
    ai_tasks = 0
    cache_hits = 0
    cache_misses = 0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    latencies = []
    feedback_counts = {"correct": 0, "inaccurate": 0}

    for row in rows:
        result = row.get("result") or {}
        metadata = result.get("ai_metadata") if isinstance(result, dict) else None
        if not isinstance(metadata, dict):
            continue
        ai_tasks += 1
        models[str(metadata.get("model") or "unknown")] += 1
        protocols[str(metadata.get("protocol") or "unknown")] += 1
        if metadata.get("cache_hit") is True:
            cache_hits += 1
        else:
            cache_misses += 1
        latency = _number(metadata.get("latency_ms"))
        if latency > 0:
            latencies.append(latency)
        usage = metadata.get("usage") or {}
        total_tokens += int(_number(usage.get("total_tokens")))
        prompt_tokens += int(
            _number(usage.get("prompt_tokens") or usage.get("input_tokens"))
        )
        completion_tokens += int(
            _number(usage.get("completion_tokens") or usage.get("output_tokens"))
        )
        feedback_counts["correct"] += int(row.get("correct_feedback") or 0)
        feedback_counts["inaccurate"] += int(row.get("inaccurate_feedback") or 0)

    return {
        "ai_tasks": ai_tasks,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_hit_rate": cache_hits / ai_tasks if ai_tasks else 0.0,
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
        },
        "models": dict(sorted(models.items())),
        "protocols": dict(sorted(protocols.items())),
        "feedback": feedback_counts,
    }
