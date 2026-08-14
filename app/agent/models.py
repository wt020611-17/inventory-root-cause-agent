"""Agent 对外契约、受控意图与模型输出模型。"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.results import AnalysisResult, EvidenceGraphResult, RiskListResult


class AnalysisIntent(StrEnum):
    """Phase 3 首版允许路由到的三个确定性分析意图。"""

    LIST_INVENTORY_RISKS = "list_inventory_risks"
    ANALYZE_MATERIAL_ROOT_CAUSE = "analyze_material_root_cause"
    TRACE_EVIDENCE = "trace_evidence"


class AgentResponseStatus(StrEnum):
    """自然语言入口对调用方暴露的稳定状态。"""

    OK = "ok"
    EMPTY = "empty"
    BLOCKED = "blocked"
    ERROR = "error"
    NEEDS_INPUT = "needs_input"
    DEGRADED = "degraded"


class _AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentParameters(_AgentModel):
    """用户已明确提供或经解析节点确认的结构化参数。"""

    material_id: str | None = Field(default=None, min_length=1, max_length=64)
    warehouse_id: str | None = Field(default=None, min_length=1, max_length=64)
    as_of_date: date | None = None
    category: str | None = Field(default=None, min_length=1, max_length=100)


class AgentRequest(_AgentModel):
    """自然语言入口请求；显式参数的优先级高于模型抽取。"""

    question: str = Field(min_length=1, max_length=2000)
    parameters: AgentParameters = Field(default_factory=AgentParameters)
    confirmed_intent: AnalysisIntent | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)


class ParsedRequest(_AgentModel):
    """输入解析节点的受控结果。"""

    intent: AnalysisIntent | None = None
    parameters: AgentParameters = Field(default_factory=AgentParameters)


class GeneratedSummary(_AgentModel):
    """LLM 只能生成展示摘要和建议，不能覆盖结构化事实。"""

    summary: str = Field(min_length=1, max_length=2000)
    suggestions: list[str] = Field(default_factory=list, max_length=5)


ToolResult = AnalysisResult | RiskListResult | EvidenceGraphResult


class AgentResponse(_AgentModel):
    """自然语言分析的最终响应，不包含私有推理链。"""

    trace_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    status: AgentResponseStatus
    message: str = Field(min_length=1)
    intent: AnalysisIntent | None = None
    parameters: AgentParameters
    missing_fields: list[str] = Field(default_factory=list)
    selected_tool: str | None = None
    result: ToolResult | None = None
    evidence: list[dict[str, object]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    action_summaries: list[str] = Field(default_factory=list)
    llm_used: bool = False
    system_prompt_version: str
    tool_schema_version: str


class ToolDescriptor(_AgentModel):
    """GET /tools 返回的受控工具目录。"""

    name: str
    description: str
    required_fields: list[str]
