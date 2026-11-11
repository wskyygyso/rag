"""检索通道与后处理器的稳定契约。"""

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Mapping, Protocol


@dataclass(frozen=True)
class RetrievalRequest:
    """描述一次只读检索请求。

    `options` 只携带通道需要的已校验参数，例如历史任务偏移或来源筛选；禁止放入
    数据库连接、密钥或未脱敏的外部请求对象。
    """

    root: str
    project: str
    query: str
    limit: int = 20
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ChannelResult:
    """单个检索通道的执行结果和可观测摘要。"""

    channel: str
    hits: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0
    degraded: bool = False
    error_type: str = ""
    error_message: str = ""

    def summary(self) -> Dict[str, Any]:
        """返回可持久化或展示的通道摘要。

        Returns:
            通道名、命中数和耗时；失败时只暴露受控错误类型和摘要消息，不返回
            可能包含路径、SQL 或密钥的异常堆栈。
        """
        result = {
            "channel": self.channel,
            "hit_count": len(self.hits),
            "elapsed_ms": round(self.elapsed_ms, 3),
            "degraded": self.degraded,
        }
        if self.error_type:
            result["error_type"] = self.error_type
        if self.error_message:
            result["error_message"] = self.error_message
        return result


class RetrievalChannel(Protocol):
    """只读检索通道协议。"""

    name: str

    async def search(self, request: RetrievalRequest) -> ChannelResult:
        """执行通道检索并返回带耗时和降级信息的命中集合。

        参数:
            - `request`：已校验的只读检索请求，其中的根目录、项目和查询词共同
              限定本次通道可以访问的数据范围；通道不得自行扩大项目边界或读取
              密钥。

        Returns:
            通道命中、耗时和降级摘要；调用方负责统一后处理与最终证据预算裁剪。
        """
        ...


class SearchPostProcessor(Protocol):
    """检索结果后处理器协议。"""

    name: str
    order: int

    def process(
        self,
        hits: List[Dict[str, Any]],
        request: RetrievalRequest,
    ) -> List[Dict[str, Any]]:
        """对当前命中集合执行确定性转换并返回新集合。

        参数:
            - `hits`：前序通道或后处理器产生的候选证据，不得被原地修改，以免
              污染其他通道的观测结果。
            - `request`：当前只读检索请求，为来源过滤和去重提供项目、查询及
              已校验选项。

        Returns:
            新的候选证据列表；返回空列表表示该后处理器有意过滤掉全部候选。
        """
        ...


def timed_channel_result(
    channel: str,
    started_at: float,
    hits: List[Dict[str, Any]],
    *,
    degraded: bool = False,
    error_type: str = "",
    error_message: str = "",
) -> ChannelResult:
    """根据单调时钟起点创建通道结果。

    参数:
        - `channel`：通道稳定名称，用于审计和指标标签。
        - `started_at`：调用通道前由 `time.perf_counter()` 记录的起点。
        - `hits`：通道返回的候选字典列表。
        - `degraded`：通道是否以降级方式完成。
        - `error_type`：降级时的稳定异常类型名。
        - `error_message`：可安全展示的短错误摘要。

    Returns:
        包含毫秒耗时的 `ChannelResult`。
    """
    return ChannelResult(
        channel=channel,
        hits=hits,
        elapsed_ms=(perf_counter() - started_at) * 1000,
        degraded=degraded,
        error_type=error_type,
        error_message=error_message,
    )
