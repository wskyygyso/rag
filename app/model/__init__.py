"""模型调用边界、候选路由和结构化输出校验。"""

from app.model.contracts import AIDiagnosis, AIDiagnosisCause, AIDiagnosisRun
from app.model.errors import LLMConfigurationError, LLMResponseError, ModelGatewayError
from app.model.gateway import ModelGateway

__all__ = [
    "AIDiagnosis",
    "AIDiagnosisCause",
    "AIDiagnosisRun",
    "LLMConfigurationError",
    "LLMResponseError",
    "ModelGatewayError",
    "ModelGateway",
]
