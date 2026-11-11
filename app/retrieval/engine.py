"""顺序执行检索通道并运行确定性后处理链。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from app.retrieval.contracts import (
    ChannelResult,
    RetrievalChannel,
    RetrievalRequest,
    SearchPostProcessor,
)


@dataclass
class RetrievalEngineResult:
    """一次检索引擎运行的最终命中、通道和处理器摘要。"""

    hits: List[Dict[str, Any]]
    channels: List[Dict[str, Any]] = field(default_factory=list)
    postprocessors: List[Dict[str, Any]] = field(default_factory=list)


class RetrievalEngine:
    """按固定顺序运行只读通道，再执行有序后处理器。

    首期默认串行执行。代码文本和符号索引会扫描同一仓库，并发调度容易争抢
    磁盘并放大尾延迟；后续可对文档、历史和远程验证等独立通道增加受控并发。
    """

    def __init__(
        self,
        channels: Sequence[RetrievalChannel],
        postprocessors: Sequence[SearchPostProcessor] = (),
        *,
        fail_open: bool = False,
    ) -> None:
        """初始化检索引擎。

        参数:
            - `channels`：按顺序执行的只读检索通道集合。
            - `postprocessors`：检索完成后执行的后处理器集合，内部按 `order` 排序。
            - `fail_open`：通道失败时是否保留空结果继续。混合查询接口默认保持
              原来的失败语义；诊断工作流可在明确允许降级时开启。
        """
        self._channels = list(channels)
        self._postprocessors = sorted(postprocessors, key=lambda item: item.order)
        self._fail_open = fail_open

    async def run(self, request: RetrievalRequest) -> RetrievalEngineResult:
        """执行全部检索通道和后处理器。

        参数:
            - `request`：已经完成输入验证的只读检索请求。

        Returns:
            最终命中和通道、后处理器摘要。`fail_open=False` 时任一通道异常会
            原样抛出；`fail_open=True` 时记录降级结果并继续。
        """
        channel_results: List[ChannelResult] = []
        hits: List[Dict[str, Any]] = []
        for channel in self._channels:
            try:
                result = await channel.search(request)
            except Exception as exc:
                if not self._fail_open:
                    raise
                result = ChannelResult(
                    channel=channel.name,
                    degraded=True,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:240],
                )
            channel_results.append(result)
            if not result.degraded:
                hits.extend(result.hits)

        processor_summaries: List[Dict[str, Any]] = []
        for processor in self._postprocessors:
            before = len(hits)
            try:
                hits = processor.process(hits, request)
            except Exception as exc:
                if not self._fail_open:
                    raise
                processor_summaries.append(
                    {
                        "processor": processor.name,
                        "before": before,
                        "after": before,
                        "degraded": True,
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            processor_summaries.append(
                {
                    "processor": processor.name,
                    "before": before,
                    "after": len(hits),
                    "degraded": False,
                }
            )
        return RetrievalEngineResult(
            hits=hits,
            channels=[result.summary() for result in channel_results],
            postprocessors=processor_summaries,
        )
