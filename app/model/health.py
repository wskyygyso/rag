"""基于 Redis 的模型候选短期熔断状态。"""

import hashlib
import json
import time
from typing import Any, Dict, Optional

from app.model.routing import ModelTarget


class ModelHealthStore:
    """记录同一 Endpoint 与模型的连续瞬时失败并执行短期熔断。

    Redis 不可用或数据结构损坏时按“未熔断”处理，模型调用仍由请求超时和最大尝试
    次数兜底。健康状态只影响 AI 叙写，不参与代码检索或确定性分析。
    """

    def __init__(
        self,
        redis_client: Optional[Any],
        *,
        failure_threshold: int,
        open_seconds: int,
    ) -> None:
        """初始化模型健康状态存储器。

        参数:
            - `redis_client`：异步 Redis 客户端；为空时完全禁用跨 Worker 熔断。
            - `failure_threshold`：连续瞬时失败达到该次数后打开熔断。
            - `open_seconds`：熔断状态保留秒数，到期后允许探测一次。
        """
        self._redis = redis_client
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds

    def _key(self, target: ModelTarget) -> str:
        """生成不暴露内部 Endpoint 明文的熔断键。

        参数:
            - `target`：当前模型目标。

        Returns:
            `rag:model-health:` 前缀的稳定 SHA-256 键。
        """
        digest = hashlib.sha256(target.health_key.encode("utf-8")).hexdigest()
        return f"rag:model-health:{digest}"

    async def is_open(self, target: ModelTarget) -> bool:
        """判断模型是否处于有效熔断窗口。

        参数:
            - `target`：本轮准备尝试的模型目标。

        Returns:
            Redis 中 `open_until` 晚于当前时间时返回真；禁用、异常或旧数据返回假。
        """
        if self._redis is None:
            return False
        try:
            state = await self._read_state(target)
            return float(state.get("open_until") or 0) > time.time()
        except Exception:
            return False

    async def record_success(self, target: ModelTarget) -> None:
        """清除模型连续失败状态。

        参数:
            - `target`：本轮成功返回结构化诊断的模型目标。
        """
        if self._redis is None:
            return
        try:
            await self._redis.delete(self._key(target))
        except Exception:
            pass

    async def record_failure(self, target: ModelTarget) -> None:
        """累计一次瞬时失败并在达到阈值后打开熔断。

        参数:
            - `target`：发生超时、限流、服务端错误或空响应的模型目标。
        """
        if self._redis is None:
            return
        try:
            state = await self._read_state(target)
            failures = int(state.get("failures") or 0) + 1
            now = time.time()
            next_state: Dict[str, Any] = {"failures": failures}
            if failures >= self._failure_threshold:
                next_state["open_until"] = now + self._open_seconds
            await self._redis.setex(
                self._key(target),
                self._open_seconds,
                json.dumps(next_state, separators=(",", ":")),
            )
        except Exception:
            pass

    async def _read_state(self, target: ModelTarget) -> Dict[str, Any]:
        """读取并解析单个模型的健康状态。

        参数:
            - `target`：需要读取状态的模型目标。

        Returns:
            Redis JSON 对象；空值、非字典或损坏 JSON 按空状态处理。
        """
        raw = await self._redis.get(self._key(target))
        if not raw:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
