"""FastAPI 传输层请求与健康响应模型。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRequest(BaseModel):
    """结构化库存分析请求；格式错误由 API 层在服务执行前拒绝。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    material_id: str = Field(min_length=1, max_length=64)
    warehouse_id: str = Field(min_length=1, max_length=64)
    as_of_date: date


class HealthResponse(BaseModel):
    """不包含内部路径、依赖细节或密钥的最小健康响应。"""

    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    trace_id: str = Field(min_length=1)


class RiskListRequest(BaseModel):
    """风险清单筛选条件；仓库与类别可选，分析日期必须显式提供。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    warehouse_id: str | None = Field(default=None, min_length=1, max_length=64)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    as_of_date: date


class InvalidRequestResponse(BaseModel):
    """API 参数错误结构；不混入领域执行状态，但始终包含 trace_id。"""

    model_config = ConfigDict(extra="forbid")

    code: str = "invalid_request"
    message: str = "请求参数校验失败"
    trace_id: str = Field(min_length=1)
    details: list[dict] = Field(default_factory=list)


class InternalErrorResponse(BaseModel):
    """未处理异常的稳定对外错误，不包含堆栈与内部细节。"""

    model_config = ConfigDict(extra="forbid")

    code: str = "internal_error"
    message: str = "服务暂时不可用，请稍后重试"
    trace_id: str = Field(min_length=1)
