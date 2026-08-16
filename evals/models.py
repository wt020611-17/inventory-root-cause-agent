"""Phase 4 Agent 评测数据集与报告模型。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent import AgentParameters, AgentResponseStatus, AnalysisIntent
from app.domain.enums import RootCauseType


class _EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvalTurn(_EvalModel):
    """一个评测轮次；同一用例的轮次共享会话。"""

    question: str = Field(min_length=1, max_length=2000)
    parameters: AgentParameters = Field(default_factory=AgentParameters)
    confirmed_intent: AnalysisIntent | None = None
    advance_clock_seconds: int = Field(default=0, ge=0)


class EvalExpectation(_EvalModel):
    """只保存可从公开 Agent 响应核验的期望，不保存私有推理。"""

    status: AgentResponseStatus
    intent: AnalysisIntent | None = None
    selected_tool: str | None = None
    parameter_fields: dict[str, str | None] = Field(default_factory=dict)
    missing_fields: list[str] | None = None
    root_causes: list[RootCauseType] | None = None
    evidence_required: bool = False
    llm_used: bool = False

    @field_validator("parameter_fields")
    @classmethod
    def validate_parameter_fields(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        allowed = {"material_id", "warehouse_id", "as_of_date", "category"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown Agent parameter fields: {sorted(unknown)}")
        return value


class AgentEvalCase(_EvalModel):
    """一条独立评测样例。"""

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    description: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    scenario: Literal["standard", "dependency_failure"] = "standard"
    safety_case: bool = False
    turns: list[EvalTurn] = Field(min_length=1)
    expected: EvalExpectation


class AgentEvalDataset(_EvalModel):
    """带固定 seed 和日期的版本化评测集。"""

    dataset_version: str = Field(min_length=1)
    seed: int
    generated_at: datetime
    as_of_date: date
    cases: list[AgentEvalCase] = Field(min_length=12)

    @field_validator("cases")
    @classmethod
    def validate_unique_case_ids(cls, value: list[AgentEvalCase]) -> list[AgentEvalCase]:
        case_ids = [case.case_id for case in value]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Agent evaluation case_id values must be unique")
        return value


class MetricResult(_EvalModel):
    """一个可审计指标的分子、分母和值。"""

    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)


class CaseOutcome(_EvalModel):
    """单用例结果，便于失败时定位到具体维度。"""

    case_id: str
    passed: bool
    actual_status: AgentResponseStatus
    actual_intent: AnalysisIntent | None
    actual_selected_tool: str | None
    tool_selection_pass: bool | None
    parameter_extraction_pass: bool
    task_completion_pass: bool
    evidence_completeness_pass: bool | None
    safe_degradation_pass: bool | None
    failures: list[str] = Field(default_factory=list)


class AgentEvalReport(_EvalModel):
    """评测命令保存的完整、可复现报告。"""

    dataset_version: str
    evaluated_at: datetime
    passed: bool
    metrics: dict[str, MetricResult]
    environment: dict[str, str]
    cases: list[CaseOutcome]
