"""输入补全、工具执行、证据校验、重试和降级的 LangGraph 工作流。"""

import re
from collections.abc import Callable
from time import perf_counter
from typing import Literal, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.config import AgentSettings
from app.agent.llm import AgentLLM, LLMUnavailableError
from app.agent.models import (
    AgentParameters,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    AnalysisIntent,
    GeneratedSummary,
    ToolResult,
)
from app.agent.parser import parse_request
from app.agent.session import InMemorySessionStore
from app.agent.state import AgentInputState, AgentState
from app.core import audit_event
from app.domain.enums import InventoryRiskLevel, ResultStatus
from app.domain.results import AnalysisResult, RiskListResult
from app.tools import (
    AnalyzeMaterialRootCauseInput,
    InventoryAgentTools,
    ListInventoryRisksInput,
    TraceEvidenceInput,
)

TOOL_DESCRIPTIONS = {
    AnalysisIntent.LIST_INVENTORY_RISKS: "按仓库或类别列出确定性库存风险清单。",
    AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE: "分析指定物料与仓库的候选根因和直接证据。",
    AnalysisIntent.TRACE_EVIDENCE: "追踪指定物料与仓库的受限业务证据路径。",
}

_REQUIRED_FIELDS = {
    AnalysisIntent.LIST_INVENTORY_RISKS: ("as_of_date",),
    AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE: (
        "material_id",
        "warehouse_id",
        "as_of_date",
    ),
    AnalysisIntent.TRACE_EVIDENCE: ("material_id", "warehouse_id", "as_of_date"),
}
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])-?\d+(?:\.\d+)?")


def build_agent_workflow(
    *,
    tools: InventoryAgentTools,
    llm: AgentLLM,
    sessions: InMemorySessionStore,
    settings: AgentSettings,
) -> CompiledStateGraph:
    """编译完整 Phase 3 工作流；所有外部依赖均可注入。"""

    def initialize_request(state: AgentInputState) -> AgentState:
        return {
            "question": state["question"],
            "parameters": state.get("parameters", AgentParameters()),
            "intent": state.get("intent"),
            "trace_id": state.get("trace_id") or uuid4().hex,
            "session_id": state.get("session_id") or uuid4().hex,
            "selected_tool": None,
            "tool_result": None,
            "execution_error": None,
            "evidence": [],
            "evidence_valid": False,
            "conclusion_allowed": False,
            "missing_fields": [],
            "generated_summary": None,
            "final_response": None,
            "retry_count": 0,
            "step_count": 1,
            "limit_reached": False,
            "llm_used": False,
            "action_summaries": ["request_initialized"],
            "system_prompt_version": settings.system_prompt_version,
            "tool_schema_version": settings.tool_schema_version,
        }

    def parse_input(state: AgentState) -> AgentState:
        context = sessions.get(state["session_id"])
        parsed, llm_used = parse_request(state["question"], llm)
        merged = _merge_parameters(
            explicit=state["parameters"],
            parsed=parsed.parameters,
            previous=context.parameters if context else AgentParameters(),
        )
        intent = state.get("intent") or parsed.intent or (context.intent if context else None)
        missing = _missing_fields(intent, merged)
        sessions.save(session_id=state["session_id"], parameters=merged, intent=intent)
        return {
            "parameters": merged,
            "intent": intent,
            "missing_fields": missing,
            "llm_used": llm_used,
            "step_count": state["step_count"] + 1,
            "action_summaries": ["input_parsed"],
        }

    def select_tool(state: AgentState) -> AgentState:
        selected = state["intent"].value if not state["missing_fields"] else None
        return {
            "selected_tool": selected,
            "step_count": state["step_count"] + 1,
            "action_summaries": [
                "required_input_requested" if selected is None else f"tool_selected:{selected}"
            ],
        }

    def execute_tool(state: AgentState) -> AgentState:
        started_at = perf_counter()
        try:
            result = _execute_selected_tool(tools, state)
            audit_event(
                "agent_tool_completed",
                trace_id=state["trace_id"],
                tool_name=state["selected_tool"],
                result_status=result.metadata.status.value,
                error_category=_result_error_category(result),
                retry_count=state["retry_count"],
                elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
            )
            return {
                "tool_result": result,
                "execution_error": None,
                "step_count": state["step_count"] + 1,
                "action_summaries": [f"tool_executed:{state['selected_tool']}"],
            }
        except Exception:
            audit_event(
                "agent_tool_failed",
                trace_id=state["trace_id"],
                level=40,
                tool_name=state["selected_tool"],
                result_status="error",
                error_category="tool_execution_error",
                retry_count=state["retry_count"],
                elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
            )
            return {
                "tool_result": None,
                "execution_error": "tool_execution_error",
                "step_count": state["step_count"] + 1,
                "action_summaries": ["tool_execution_failed"],
            }

    def validate_evidence(state: AgentState) -> AgentState:
        evidence, valid, allowed = _validate_tool_result(state.get("tool_result"))
        return {
            "evidence": evidence,
            "evidence_valid": valid,
            "conclusion_allowed": allowed,
            "step_count": state["step_count"] + 1,
            "action_summaries": ["evidence_validated" if valid else "evidence_rejected"],
        }

    def retry_query(state: AgentState) -> AgentState:
        params = state["parameters"].model_copy(update={"category": None})
        return {
            "parameters": params,
            "tool_result": None,
            "retry_count": state["retry_count"] + 1,
            "step_count": state["step_count"] + 1,
            "action_summaries": ["empty_result_retry_without_category"],
        }

    def generate_summary(state: AgentState) -> AgentState:
        result = state.get("tool_result")
        generated: GeneratedSummary | None = None
        action = "template_summary_selected"
        llm_used = state.get("llm_used", False)
        if result is not None and llm.available and state.get("conclusion_allowed", False):
            result_json = result.model_dump_json()
            try:
                candidate = llm.summarize(result_json)
                llm_used = True
                if _summary_numbers_valid(candidate, result_json):
                    generated = candidate
                    action = "llm_summary_accepted"
                else:
                    action = "llm_summary_rejected"
            except LLMUnavailableError:
                action = "llm_summary_unavailable"
        return {
            "generated_summary": generated,
            "llm_used": llm_used,
            "step_count": state["step_count"] + 1,
            "action_summaries": [action],
        }

    def organize_result(state: AgentState) -> AgentState:
        response = _build_response(state, settings)
        return {
            "final_response": response,
            "step_count": state["step_count"] + 1,
            "action_summaries": ["response_organized"],
        }

    def safe_fallback(state: AgentState) -> AgentState:
        response = AgentResponse(
            trace_id=state["trace_id"],
            session_id=state["session_id"],
            status=AgentResponseStatus.DEGRADED,
            message="Agent 已达到最大执行步数，已安全停止；请缩小问题范围后重试。",
            intent=state.get("intent"),
            parameters=state.get("parameters", AgentParameters()),
            missing_fields=state.get("missing_fields", []),
            selected_tool=state.get("selected_tool"),
            result=state.get("tool_result"),
            evidence=state.get("evidence", []),
            action_summaries=[*state.get("action_summaries", []), "execution_limit_reached"],
            system_prompt_version=settings.system_prompt_version,
            tool_schema_version=settings.tool_schema_version,
        )
        return {
            "limit_reached": True,
            "final_response": response,
            "action_summaries": ["execution_limit_reached"],
        }

    def after_standard(next_node: str) -> Callable[[AgentState], str]:
        def route(state: AgentState) -> str:
            return "safe_fallback" if state["step_count"] >= settings.agent_max_steps else next_node

        return route

    def after_selection(
        state: AgentState,
    ) -> Literal["safe_fallback", "organize_result", "execute_tool"]:
        if state["step_count"] >= settings.agent_max_steps:
            return "safe_fallback"
        if state["missing_fields"]:
            return "organize_result"
        return "execute_tool"

    def after_validation(
        state: AgentState,
    ) -> Literal["safe_fallback", "retry_query", "generate_summary"]:
        if state["step_count"] >= settings.agent_max_steps:
            return "safe_fallback"
        if _should_retry(state, settings):
            return "retry_query"
        return "generate_summary"

    builder = StateGraph(AgentState, input_schema=AgentInputState)
    builder.add_node("initialize_request", initialize_request)
    builder.add_node("parse_input", parse_input)
    builder.add_node("select_tool", select_tool)
    builder.add_node("execute_tool", execute_tool)
    builder.add_node("validate_evidence", validate_evidence)
    builder.add_node("retry_query", retry_query)
    builder.add_node("generate_summary", generate_summary)
    builder.add_node("organize_result", organize_result)
    builder.add_node("safe_fallback", safe_fallback)
    builder.add_edge(START, "initialize_request")
    builder.add_conditional_edges("initialize_request", after_standard("parse_input"))
    builder.add_conditional_edges("parse_input", after_standard("select_tool"))
    builder.add_conditional_edges("select_tool", after_selection)
    builder.add_conditional_edges("execute_tool", after_standard("validate_evidence"))
    builder.add_conditional_edges("validate_evidence", after_validation)
    builder.add_conditional_edges("retry_query", after_standard("execute_tool"))
    builder.add_conditional_edges("generate_summary", after_standard("organize_result"))
    builder.add_edge("organize_result", END)
    builder.add_edge("safe_fallback", END)
    return builder.compile()


def invoke_agent(
    request: AgentRequest,
    *,
    tools: InventoryAgentTools,
    llm: AgentLLM,
    sessions: InMemorySessionStore,
    settings: AgentSettings,
) -> AgentResponse:
    """执行一次自然语言库存分析并返回严格响应模型。"""
    workflow = build_agent_workflow(tools=tools, llm=llm, sessions=sessions, settings=settings)
    result = cast(
        AgentState,
        workflow.invoke(
            {
                "question": request.question,
                "parameters": request.parameters,
                "intent": request.confirmed_intent,
                "session_id": request.session_id,
                "trace_id": request.trace_id,
            }
        ),
    )
    return result["final_response"]


def _merge_parameters(
    *,
    explicit: AgentParameters,
    parsed: AgentParameters,
    previous: AgentParameters,
) -> AgentParameters:
    """当前显式参数优先，其次当前原文抽取，最后才使用未过期会话。"""
    return AgentParameters(
        material_id=explicit.material_id or parsed.material_id or previous.material_id,
        warehouse_id=explicit.warehouse_id or parsed.warehouse_id or previous.warehouse_id,
        as_of_date=explicit.as_of_date or parsed.as_of_date or previous.as_of_date,
        category=explicit.category or parsed.category or previous.category,
    )


def _missing_fields(intent: AnalysisIntent | None, params: AgentParameters) -> list[str]:
    if intent is None:
        return ["intent"]
    return [field for field in _REQUIRED_FIELDS[intent] if getattr(params, field) is None]


def _execute_selected_tool(tools: InventoryAgentTools, state: AgentState) -> ToolResult:
    params = state["parameters"]
    trace_id = state["trace_id"]
    intent = state["intent"]
    if intent is AnalysisIntent.LIST_INVENTORY_RISKS:
        return tools.list_inventory_risks(
            ListInventoryRisksInput(
                warehouse_id=params.warehouse_id,
                category=params.category,
                as_of_date=params.as_of_date,
                trace_id=trace_id,
            )
        )
    if intent is AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE:
        return tools.analyze_material_root_cause(
            AnalyzeMaterialRootCauseInput(
                material_id=params.material_id,
                warehouse_id=params.warehouse_id,
                as_of_date=params.as_of_date,
                trace_id=trace_id,
            )
        )
    return tools.trace_evidence(
        TraceEvidenceInput(
            material_id=params.material_id,
            warehouse_id=params.warehouse_id,
            as_of_date=params.as_of_date,
            trace_id=trace_id,
        )
    )


def _validate_tool_result(
    result: ToolResult | None,
) -> tuple[list[dict[str, object]], bool, bool]:
    if result is None:
        return [], False, False
    status = result.metadata.status
    if status is ResultStatus.ERROR:
        return [], True, False
    if status is ResultStatus.EMPTY:
        return [], True, False
    if isinstance(result, AnalysisResult):
        evidence = [item.model_dump(mode="json") for item in result.evidence]
        if status is ResultStatus.BLOCKED:
            return evidence, True, False
        supported = [
            candidate
            for candidate in result.root_causes
            if not candidate.insufficient_evidence
        ]
        # 正常物料可以没有命中的根因候选；非正常风险若没有任何受支持候选，
        # 则仍应阻断结论，避免把“证据不足”误当成“没有根因”。
        normal_without_cause = bool(
            not supported
            and result.risk
            and result.risk.risk_level is InventoryRiskLevel.NORMAL
        )
        valid = normal_without_cause or bool(
            supported and all(candidate.evidence for candidate in supported)
        )
        return evidence, valid, valid
    if isinstance(result, RiskListResult):
        evidence = [
            item.model_dump(mode="json")
            for analysis in result.items
            for item in analysis.evidence
        ]
        valid = bool(evidence) and all(analysis.evidence for analysis in result.items)
        return evidence, valid, valid
    evidence = [
        *({"kind": "node", **item.model_dump(mode="json")} for item in result.nodes),
        *({"kind": "edge", **item.model_dump(mode="json")} for item in result.edges),
        *({"kind": "path", **item.model_dump(mode="json")} for item in result.paths),
    ]
    valid = status is ResultStatus.OK and bool(result.paths)
    return evidence, valid, valid


def _should_retry(state: AgentState, settings: AgentSettings) -> bool:
    result = state.get("tool_result")
    return bool(
        isinstance(result, RiskListResult)
        and result.metadata.status is ResultStatus.EMPTY
        and state["parameters"].category
        and state["retry_count"] < settings.agent_max_retries
    )


def _summary_numbers_valid(summary: GeneratedSummary, result_json: str) -> bool:
    allowed = set(_NUMBER_PATTERN.findall(result_json))
    generated = set(_NUMBER_PATTERN.findall(summary.summary))
    for suggestion in summary.suggestions:
        generated.update(_NUMBER_PATTERN.findall(suggestion))
    return generated <= allowed


def _build_response(state: AgentState, settings: AgentSettings) -> AgentResponse:
    result = state.get("tool_result")
    generated = state.get("generated_summary")
    if state["missing_fields"]:
        status = AgentResponseStatus.NEEDS_INPUT
        message = _missing_message(state["missing_fields"])
        suggestions: list[str] = []
    elif state.get("execution_error") or result is None:
        status = AgentResponseStatus.ERROR
        message = "分析执行失败，请稍后重试。"
        suggestions = []
    elif result.metadata.status is ResultStatus.EMPTY:
        status = AgentResponseStatus.EMPTY
        message = "查询成功，但没有匹配数据；请核对物料、仓库或分析日期。"
        suggestions = ["核对查询参数后重试"]
    elif result.metadata.status is ResultStatus.ERROR:
        status = AgentResponseStatus.ERROR
        message = "分析依赖执行失败，请稍后重试。"
        suggestions = []
    elif result.metadata.status is ResultStatus.BLOCKED or not state["conclusion_allowed"]:
        status = AgentResponseStatus.BLOCKED
        message = "数据质量或证据不足，已保留事实但不输出根因结论。"
        suggestions = ["先修复数据质量问题或补充证据"]
    else:
        status = AgentResponseStatus.OK
        message = generated.summary if generated else "已完成确定性分析，详见结构化指标与证据。"
        suggestions = generated.suggestions if generated else ["结合证据核对候选根因"]
    return AgentResponse(
        trace_id=state["trace_id"],
        session_id=state["session_id"],
        status=status,
        message=message,
        intent=state.get("intent"),
        parameters=state["parameters"],
        missing_fields=state["missing_fields"],
        selected_tool=state.get("selected_tool"),
        result=result,
        evidence=state.get("evidence", []),
        suggestions=suggestions,
        action_summaries=[*state.get("action_summaries", []), "response_organized"],
        llm_used=state.get("llm_used", False),
        system_prompt_version=settings.system_prompt_version,
        tool_schema_version=settings.tool_schema_version,
    )


def _missing_message(fields: list[str]) -> str:
    labels = {
        "intent": "要执行风险清单、根因分析还是证据追踪",
        "material_id": "物料 ID",
        "warehouse_id": "仓库 ID",
        "as_of_date": "分析日期（YYYY-MM-DD）",
    }
    return "请补充：" + "、".join(labels[field] for field in fields) + "。"


def _result_error_category(result: ToolResult) -> str | None:
    errors = getattr(result, "errors", [])
    if errors:
        return str(errors[0])
    blockers = getattr(result, "blockers", [])
    if blockers:
        return str(blockers[0])
    return None
