"""诊断应用流水线入口。"""

from collections.abc import Awaitable, Callable
from typing import Any, Optional

from app.application.diagnosis.context import DiagnosisContext
from app.application.diagnosis.events import DiagnosisEvent, DiagnosisEventType
from app.diagnosis_workflow import execute_diagnosis
from app.settings import get_settings


DiagnosisExecutor = Callable[[str], Awaitable[None]]
EventSink = Callable[[DiagnosisEvent], Any]


class DiagnosisPipeline:
    """在统一上下文和事件边界内执行一次诊断任务。

    当前执行器复用已经验证的代码检索与证据分析工作流。Pipeline 负责任务级
    生命周期、超时上下文和事件，后续阶段可继续把内部步骤拆成独立 Stage，
    而不改变 Worker 调用入口。
    """

    def __init__(
        self,
        *,
        executor: DiagnosisExecutor = execute_diagnosis,
        event_sink: Optional[EventSink] = None,
    ) -> None:
        """初始化诊断流水线。

        参数:
            - `executor`：接收任务 ID 并执行诊断的异步函数；测试可注入替身。
            - `event_sink`：可选事件消费者。事件写入失败会抛给上层，避免认为
              追踪成功但实际丢失任务生命周期。
        """
        self._executor = executor
        self._event_sink = event_sink
        self._settings = get_settings()

    def _emit(self, event: DiagnosisEvent) -> None:
        """把事件发送给可选消费者。

        参数:
            - `event`：当前诊断阶段事件。没有消费者时不执行任何操作。
        """
        if self._event_sink is not None:
            self._event_sink(event)

    async def run(self, task_id: str) -> DiagnosisContext:
        """执行诊断任务并返回生命周期上下文。

        参数:
            - `task_id`：已持久化的诊断任务 ID，不能为空。

        Returns:
            已完成或失败状态对应的上下文。执行器的业务异常会原样重新抛出，
            以便 Celery 和既有任务状态处理保持原语义。
        """
        if not task_id:
            raise ValueError("诊断任务 ID 不能为空")
        context = DiagnosisContext(task_id=task_id)
        context.bind_timeout(self._settings.diagnosis_task_soft_time_limit_seconds)
        context.current_stage = "starting"
        self._emit(
            DiagnosisEvent(
                event_type=DiagnosisEventType.TASK_STARTED,
                task_id=task_id,
                stage=context.current_stage,
            )
        )
        try:
            await self._executor(task_id)
        except Exception as exc:
            context.current_stage = "failed"
            context.metadata["error_type"] = type(exc).__name__
            self._emit(
                DiagnosisEvent(
                    event_type=DiagnosisEventType.TASK_FAILED,
                    task_id=task_id,
                    stage=context.current_stage,
                    payload={"error_type": type(exc).__name__},
                )
            )
            raise
        context.current_stage = "completed"
        self._emit(
            DiagnosisEvent(
                event_type=DiagnosisEventType.TASK_COMPLETED,
                task_id=task_id,
                stage=context.current_stage,
            )
        )
        return context
