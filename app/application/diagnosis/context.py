"""单次诊断运行上下文。"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


@dataclass
class DiagnosisContext:
    """保存一次诊断在阶段之间共享的身份、预算和运行状态。

    上下文只承载应用层编排数据，不包含数据库连接池、Redis 客户端或 FastAPI
    请求对象。这样诊断流程可以在 Worker、测试和未来同步 API 中复用。
    """

    task_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deadline_at: Optional[datetime] = None
    current_stage: str = "created"
    metadata: dict[str, Any] = field(default_factory=dict)

    def bind_timeout(self, timeout_seconds: float) -> None:
        """按任务软超时计算本次上下文截止时间。

        参数:
            - `timeout_seconds`：诊断任务允许使用的软超时秒数，必须为正数。

        Raises:
            ValueError：超时配置不是正数时抛出，避免创建无预算运行。
        """
        if timeout_seconds <= 0:
            raise ValueError("诊断上下文超时必须为正数")
        self.deadline_at = self.started_at + timedelta(seconds=timeout_seconds)

    def remaining_seconds(self, now: Optional[datetime] = None) -> Optional[float]:
        """返回距离阶段截止时间还剩多少秒。

        参数:
            - `now`：用于测试或外部时钟注入的当前时间；为空时读取 UTC 当前时间。

        Returns:
            未配置截止时间时返回 `None`，否则返回可能为负数的剩余秒数。
        """
        if self.deadline_at is None:
            return None
        current = now or datetime.now(timezone.utc)
        return (self.deadline_at - current).total_seconds()
