"""检索通道与后处理链单元测试。"""

import pytest
from typing import Optional

from app.retrieval.contracts import ChannelResult, RetrievalRequest
from app.retrieval.engine import RetrievalEngine
from app.retrieval.postprocessors import SourceFilterPostProcessor, WeightedDeduplicationPostProcessor


class _Channel:
    """返回固定命中或异常的可控检索通道。"""

    def __init__(self, name: str, hits: list[dict], error: Optional[Exception] = None) -> None:
        """初始化通道替身。

        参数:
            - `name`：通道名称。
            - `hits`：成功时返回的命中集合。
            - `error`：设置后模拟通道异常。
        """
        self.name = name
        self._hits = hits
        self._error = error

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """返回固定命中或抛出指定异常。

        参数:
            - `request`：当前检索请求。
        """
        if self._error is not None:
            raise self._error
        return ChannelResult(channel=self.name, hits=self._hits)


@pytest.mark.asyncio
async def test_engine_runs_sources_and_weighted_postprocessors() -> None:
    """验证通道结果按来源过滤、去重、加权和稳定排序。"""
    engine = RetrievalEngine(
        [
            _Channel("code", [{"source_type": "code", "source_id": "a.php:1", "score": 1.0}]),
            _Channel("history", [{"source_type": "history", "source_id": "diag_1", "score": 0.6}]),
        ],
        [
            SourceFilterPostProcessor(["code", "history"]),
            WeightedDeduplicationPostProcessor({"code": 1.0, "history": 0.5}),
        ],
    )

    result = await engine.run(RetrievalRequest(root="/repo", project="waky3", query="invite", limit=10))

    assert [item["source_id"] for item in result.hits] == ["a.php:1", "diag_1"]
    assert [item["channel"] for item in result.channels] == ["code", "history"]
    assert [item["processor"] for item in result.postprocessors] == [
        "source_filter",
        "weighted_deduplication",
    ]


@pytest.mark.asyncio
async def test_fail_open_channel_returns_degraded_summary() -> None:
    """验证允许降级的通道失败不会中断其他结果。"""
    engine = RetrievalEngine(
        [
            _Channel("broken", [], RuntimeError("timeout")),
            _Channel("code", [{"source_type": "code", "source_id": "a.php:1", "score": 1.0}]),
        ],
        fail_open=True,
    )

    result = await engine.run(RetrievalRequest(root="/repo", project="waky3", query="invite"))

    assert len(result.hits) == 1
    assert result.channels[0]["degraded"] is True
    assert result.channels[0]["error_type"] == "RuntimeError"
