"""无外部模型依赖的确定性文本向量工具。

该实现用于向量检索链路的 MVP 和离线测试；生产环境可替换为真实 embedding
服务，只需保持固定维度的浮点数组接口。
"""

import hashlib
import math
import re
from typing import List, Optional, Protocol

EMBEDDING_DIMENSION = 64
EMBEDDING_MODEL = "hash-v1"


class EmbeddingProvider(Protocol):
    """可插拔 embedding 提供方契约。"""

    model_name: str
    dimension: int

    def embed(self, text: str) -> List[float]:
        """将文本转换为固定维度的归一化向量。

        参数:
            - `text`：待生成向量的文本，类型为 `str`。
        """


class DeterministicEmbeddingProvider:
    """基于哈希特征的本地 embedding 提供方，适合 MVP 和离线测试。"""

    model_name = EMBEDDING_MODEL

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        """创建指定维度的确定性向量提供方。

        参数:
            - `dimension`：向量维度，类型为 `int`。
        """
        if dimension < 8 or dimension > 2048:
            raise ValueError("向量维度必须在 8 到 2048 之间")
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        """生成归一化哈希特征向量。

        参数:
            - `text`：待生成向量的文本，类型为 `str`。
        """
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_:]*|[\u4e00-\u9fff]{2,}", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def get_embedding_provider(name: str = EMBEDDING_MODEL, dimension: int = EMBEDDING_DIMENSION) -> EmbeddingProvider:
    """按名称返回 embedding 提供方；未知提供方明确报错，避免静默混用模型。

    参数:
        - `name`：名称或标识，类型为 `str`。
        - `dimension`：向量维度，类型为 `int`。
    """
    if name != EMBEDDING_MODEL:
        raise ValueError(f"暂不支持 embedding 提供方: {name}")
    return DeterministicEmbeddingProvider(dimension)


def embed_text(text: str, dimension: int = EMBEDDING_DIMENSION) -> List[float]:
    """使用哈希特征生成归一化文本向量，保证同一文本结果稳定。

    参数:
        - `text`：待生成向量的文本，类型为 `str`。
        - `dimension`：向量维度，类型为 `int`。
    """
    return get_embedding_provider(dimension=dimension).embed(text)


def cosine_similarity(left: List[float], right: Optional[List[float]]) -> float:
    """计算同维向量余弦相似度；缺失或维度不一致时返回零。

    参数:
        - `left`：余弦相似度左向量，类型为 `List[float]`。
        - `right`：余弦相似度右向量，类型为 `Optional[List[float]]`。
    """
    if not right or len(left) != len(right):
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
