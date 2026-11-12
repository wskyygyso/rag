"""确定性业务分析器注册表。"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set

from app.domain.diagnosis.analyzers.activity import _analyze_spaceship_fragments
from app.domain.diagnosis.analyzers.common import EvidenceLine
from app.domain.diagnosis.analyzers.configuration import _analyze_vip_prize_levels
from app.domain.diagnosis.analyzers.display import _analyze_frontend_diamond_display
from app.domain.diagnosis.analyzers.exchange import _analyze_diamond_exchange
from app.domain.diagnosis.analyzers.registration import _analyze_device_registration_limit
from app.domain.diagnosis.analyzers.risk_control import _analyze_usd_fuse
from app.domain.diagnosis.analyzers.withdrawal import (
    _analyze_anchor_withdrawal,
    _analyze_diamond_withdrawal,
)


AnalyzerResult = List[Dict[str, object]]
AnalyzerCallable = Callable[[Sequence[EvidenceLine]], AnalyzerResult]
AnalyzerPredicate = Callable[[str, Set[str]], bool]


@dataclass(frozen=True)
class AnalyzerSpec:
    """描述一个按可组合问题特征触发的分析器。

    `matches` 只能读取规范化问题文本和通用概念，不允许绑定项目名、完整问句、
    具体类名或答案；`analyze` 只读取已检索源码证据并返回结构化事实。
    """

    analyzer_id: str
    topic: str
    matches: AnalyzerPredicate
    analyze: AnalyzerCallable


class AnalyzerRegistry:
    """按注册顺序选择首个匹配的确定性分析器。"""

    def __init__(self, analyzers: Sequence[AnalyzerSpec]) -> None:
        """初始化分析器注册表。

        参数:
            - `analyzers`：按业务优先级排列的分析器声明。顺序必须稳定，避免
              同一问题在不同 Worker 上选择不同分析器。
        """
        self._analyzers = list(analyzers)

    def select(self, question: str, concepts: Set[str]) -> Optional[AnalyzerSpec]:
        """选择首个满足可组合特征的业务分析器。

        参数:
            - `question`：已 casefold 的用户问题，仅用于通用业务词判断。
            - `concepts`：问题中识别出的可组合概念名集合。

        Returns:
            匹配的分析器；没有领域分析器匹配时返回 `None`，由调用方进入显式
            符号或通用控制点兜底。
        """
        for analyzer in self._analyzers:
            if analyzer.matches(question, concepts):
                return analyzer
        return None

    def register(self, analyzer: AnalyzerSpec) -> None:
        """在内存注册表末尾追加一个分析器插件。

        参数:
            - `analyzer`：已声明唯一 ID、适用特征和分析函数的分析器。新增插件
              追加到末尾，保持既有问题仍由原优先级最高的分析器处理。

        Raises:
            ValueError: 分析器 ID 为空或与已注册插件重复时抛出，避免同一问题在
                不同调用路径命中实现不一致的分析器。
        """
        if not analyzer.analyzer_id.strip():
            raise ValueError("分析器 ID 不能为空")
        if analyzer.analyzer_id in self.analyzer_ids:
            raise ValueError(f"分析器 ID 已注册：{analyzer.analyzer_id}")
        self._analyzers.append(analyzer)

    @property
    def analyzer_ids(self) -> List[str]:
        """返回当前注册的分析器 ID，供审计和测试确认插件清单。"""
        return [item.analyzer_id for item in self._analyzers]


ANALYZER_REGISTRY = AnalyzerRegistry(
    [
        AnalyzerSpec(
            analyzer_id="usd_fuse",
            topic="美元熔断",
            matches=lambda question, concepts: "美元" in question and "熔断" in question,
            analyze=_analyze_usd_fuse,
        ),
        AnalyzerSpec(
            analyzer_id="device_registration",
            topic="设备注册上限",
            matches=lambda question, concepts: {
                "device",
                "registration",
            }.issubset(concepts)
            and bool(concepts.intersection({"count", "limit"})),
            analyze=_analyze_device_registration_limit,
        ),
        AnalyzerSpec(
            analyzer_id="frontend_diamond_display",
            topic="前端钻石展示",
            matches=lambda question, concepts: "钻石" in question
            and any(word in question for word in ("前端", "钱包", "页面"))
            and any(word in question for word in ("展示", "显示")),
            analyze=_analyze_frontend_diamond_display,
        ),
        AnalyzerSpec(
            analyzer_id="diamond_withdrawal",
            topic="钻石提现",
            # 用户常用“钻石兑换美元”描述官方提现，问题中不一定出现“提现”。
            # 这里按“钻石 + 官方 + 美元”的概念组合识别同一业务链路，同时不
            # 把“钻石兑金币”误路由到美元提现分析器。
            matches=lambda question, concepts: "diamond" in concepts
            and (
                "withdrawal" in concepts
                or {"official", "usd"}.issubset(concepts)
            ),
            analyze=_analyze_diamond_withdrawal,
        ),
        AnalyzerSpec(
            analyzer_id="anchor_withdrawal",
            topic="当前仓库主播钻石提现",
            matches=lambda question, concepts: False,
            analyze=_analyze_anchor_withdrawal,
        ),
        AnalyzerSpec(
            analyzer_id="diamond_exchange",
            topic="钻石兑金币",
            matches=lambda question, concepts: "钻石" in question
            and ("兑金币" in question or "兑换金币" in question),
            analyze=_analyze_diamond_exchange,
        ),
        AnalyzerSpec(
            analyzer_id="spaceship_fragments",
            topic="飞船商品碎片分配",
            matches=lambda question, concepts: "飞船" in question and "碎片" in question,
            analyze=_analyze_spaceship_fragments,
        ),
        AnalyzerSpec(
            analyzer_id="vip_prize_levels",
            topic="VIP等级附赠奖品",
            # 只有“贵族/VIP”领域概念加上枚举动作才进入该分析器，避免把所有
            # 带“等级”的问题都路由到 VIP 配置。
            matches=lambda question, concepts: "noble" in concepts
            and any(
                word in question
                for word in ("每个", "各等级", "所有", "全部", "列出", "分别")
            ),
            analyze=_analyze_vip_prize_levels,
        ),
    ]
)
