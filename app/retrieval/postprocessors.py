"""混合检索通用后处理器。"""

from typing import Any, Dict, List, Mapping, Optional

from app.retrieval.contracts import RetrievalRequest


class SourceFilterPostProcessor:
    """按来源类型过滤候选。

    未配置来源白名单时保持所有命中，支持混合查询接口的可选筛选。
    """

    name = "source_filter"
    order = 10

    def __init__(self, source_types: Optional[List[str]]) -> None:
        """初始化来源过滤器。

        参数:
            - `source_types`：允许保留的来源类型；`None` 表示不过滤。
        """
        self._source_types = source_types

    def process(self, hits: List[Dict[str, Any]], request: RetrievalRequest) -> List[Dict[str, Any]]:
        """保留白名单来源。

        参数:
            - `hits`：已合并候选集合。
            - `request`：当前检索请求，当前处理器不修改请求。

        Returns:
            符合来源筛选的新候选列表。
        """
        if self._source_types is None:
            return hits
        allowed = set(self._source_types)
        return [item for item in hits if item.get("source_type") in allowed]


class WeightedDeduplicationPostProcessor:
    """按来源权重计算最终分数并去重排序。"""

    name = "weighted_deduplication"
    order = 20

    def __init__(self, source_weights: Mapping[str, float]) -> None:
        """初始化加权去重器。

        参数:
            - `source_weights`：来源类型到权重倍率的映射，值必须为非负数。
        """
        self._source_weights = {str(key): float(value) for key, value in source_weights.items()}

    def process(self, hits: List[Dict[str, Any]], request: RetrievalRequest) -> List[Dict[str, Any]]:
        """保留同来源同 ID 的首条命中，并按加权分数排序。

        参数:
            - `hits`：已完成来源过滤的候选集合。
            - `request`：当前检索请求，其 `limit` 控制最终返回数量。

        Returns:
            最多 `request.limit` 条稳定排序的新字典列表。
        """
        unique: Dict[str, Dict[str, Any]] = {}
        for hit in hits:
            item = dict(hit)
            source_type = str(item.get("source_type") or "")
            raw_score = float(item.get("score") or 0.0)
            item["raw_score"] = raw_score
            item["score"] = round(raw_score * self._source_weights.get(source_type, 0.0), 6)
            key = f"{source_type}:{item.get('source_id')}"
            unique.setdefault(key, item)
        return sorted(unique.values(), key=lambda item: (-float(item["score"]), str(item["source_id"])))[
            : request.limit
        ]
