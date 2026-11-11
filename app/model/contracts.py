"""模型网关与证据约束叙写共享的数据契约。"""

from dataclasses import dataclass
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class AIDiagnosisCause(BaseModel):
    """AI 给出的候选原因及其必须来自证据上下文的引用。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    reasoning: str = Field(min_length=1, max_length=2000)
    evidence_refs: List[str] = Field(min_length=1, max_length=8)


class AIDiagnosis(BaseModel):
    """经过 JSON Schema 和证据引用校验的诊断叙写。"""

    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(min_length=1, max_length=3000)
    possible_causes: List[AIDiagnosisCause] = Field(max_length=5)
    uncertainties: List[str] = Field(max_length=10)
    recommended_actions: List[str] = Field(max_length=10)


@dataclass(frozen=True)
class AIDiagnosisRun:
    """保存已验证诊断结果及其不含密钥的调用元数据。"""

    diagnosis: AIDiagnosis
    metadata: Dict[str, Any]
