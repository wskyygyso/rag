"""诊断任务中按需执行的数据库、配置和日志验证计划模型。"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DatabaseValidationPlan(BaseModel):
    """描述一次只读数据库验证请求及其结果边界。"""

    sql: str = Field(min_length=1, max_length=10000)
    params: Optional[List[Any]] = Field(default=None, max_length=20)
    max_rows: int = Field(default=100, ge=1, le=1000)
    max_result_bytes: int = Field(default=512_000, ge=1, le=5_000_000)
    project: Optional[str] = Field(default=None, max_length=100)


class ConfigValidationPlan(BaseModel):
    """描述一次项目配置文件读取请求。"""

    project: str = Field(min_length=1, max_length=100)
    file: str = Field(min_length=1, max_length=500)


class LogValidationPlan(BaseModel):
    """描述一次项目日志关键词检索请求。"""

    project: str = Field(min_length=1, max_length=100)
    file: str = Field(min_length=1, max_length=500)
    query: str = Field(min_length=1, max_length=200)
    max_results: int = Field(default=100, ge=1, le=1000)


class ValidationPlan(BaseModel):
    """聚合诊断任务可选的数据库、配置和日志验证步骤。"""

    database: Optional[DatabaseValidationPlan] = None
    configs: List[ConfigValidationPlan] = Field(default_factory=list, max_length=20)
    logs: List[LogValidationPlan] = Field(default_factory=list, max_length=20)


def validation_plan_dict(plan: Optional[ValidationPlan]) -> Optional[Dict[str, Any]]:
    """把验证计划转换为可写入 JSONB 的字典；未请求验证时返回 None。"""
    if plan is None:
        return None
    return plan.model_dump(mode="json")
