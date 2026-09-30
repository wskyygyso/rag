"""无外部模型依赖的确定性文本向量工具。

该实现用于向量检索链路的 MVP 和离线测试；生产环境可替换为真实 embedding
服务，只需保持固定维度的浮点数组接口。
"""

import hashlib
import math
import re
from typing import List, Optional

EMBEDDING_DIMENSION = 64
EMBEDDING_MODEL = "hash-v1"


def embed_text(text: str, dimension: int = EMBEDDING_DIMENSION) -> List[float]:
    """使用哈希特征生成归一化文本向量，保证同一文本结果稳定。"""
    if dimension < 8 or dimension > 2048:
        raise ValueError("向量维度必须在 8 到 2048 之间")
    vector = [0.0] * dimension
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_:]*|[\u4e00-\u9fff]{2,}", text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine_similarity(left: List[float], right: Optional[List[float]]) -> float:
    """计算同维向量余弦相似度；缺失或维度不一致时返回零。"""
    if not right or len(left) != len(right):
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
