"""LangGraph 节点共享的 Agent State。"""

import operator
from typing import Annotated, NotRequired, TypedDict

from app.agent.models import (
    AgentParameters,
    AgentResponse,
    AnalysisIntent,
    GeneratedSummary,
    ToolResult,
)


class AgentInputState(TypedDict):
    """工作流只接受请求层已经校验过的字段。"""

    question: str
    parameters: NotRequired[AgentParameters]
    intent: NotRequired[AnalysisIntent | None]
    session_id: NotRequired[str | None]
    trace_id: NotRequired[str | None]


class AgentState(AgentInputState, total=False):
    """贯穿参数补全、工具执行、证据校验和结果组织的最小状态。"""

    selected_tool: str | None
    tool_result: ToolResult | None
    execution_error: str | None
    evidence: list[dict[str, object]]
    evidence_valid: bool
    conclusion_allowed: bool
    missing_fields: list[str]
    generated_summary: GeneratedSummary | None
    final_response: AgentResponse | None
    retry_count: int
    step_count: int
    limit_reached: bool
    llm_used: bool
    action_summaries: Annotated[list[str], operator.add]
    system_prompt_version: str
    tool_schema_version: str
