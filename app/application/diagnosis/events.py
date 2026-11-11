"""诊断流水线阶段事件契约。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DiagnosisEventType(str, Enum):
    """诊断运行事件类型。"""

    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"


@dataclass(frozen=True)
class DiagnosisEvent:
    """描述一次可被 SSE、审计或 Trace 消费的诊断事件。

    `payload` 只能保存脱敏摘要和统计，不得包含密钥、完整配置、Cookie、SQL
    参数或未经裁剪的业务源码。
    """

    event_type: DiagnosisEventType
    task_id: str
    stage: str
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的事件结构。

        Returns:
            事件类型、任务 ID、阶段和负载组成的字典。枚举值转换为字符串，
            调用方可直接写入日志或 SSE data 字段。
        """
        return {
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "stage": self.stage,
            "payload": dict(self.payload),
        }
