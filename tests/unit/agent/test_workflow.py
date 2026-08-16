"""Phase 3 工具选择、补参、校验、重试与降级测试。"""

from datetime import date

from app.agent import (
    AgentParameters,
    AgentRequest,
    AgentResponseStatus,
    AnalysisIntent,
    DisabledAgentLLM,
    invoke_agent,
)
from app.agent.models import GeneratedSummary, ParsedRequest
from app.domain.enums import ResultStatus


class MockLLM:
    def __init__(
        self,
        *,
        parsed: ParsedRequest | None = None,
        summary: GeneratedSummary | None = None,
    ) -> None:
        self.parsed = parsed or ParsedRequest()
        self.summary = summary or GeneratedSummary(summary="分析完成", suggestions=[])

    @property
    def available(self) -> bool:
        return True

    def parse(self, question: str) -> ParsedRequest:
        del question
        return self.parsed

    def summarize(self, result_json: str) -> GeneratedSummary:
        del result_json
        return self.summary


def _request() -> AgentRequest:
    return AgentRequest(
        question="分析该物料的库存根因",
        parameters=AgentParameters(
            material_id="MAT-SYN-MULTI",
            warehouse_id="WH-SYN-01",
            as_of_date=date(2026, 3, 31),
        ),
        confirmed_intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
        trace_id="trace-phase3",
    )


def test_selects_and_executes_root_cause_tool_without_llm(
    agent_tools, session_store, agent_settings
) -> None:
    response = invoke_agent(
        _request(),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert response.status is AgentResponseStatus.OK
    assert response.selected_tool == "analyze_material_root_cause"
    assert response.result.metadata.status is ResultStatus.OK
    assert response.evidence
    assert response.llm_used is False
    assert "tool_executed:analyze_material_root_cause" in response.action_summaries


def test_missing_parameters_only_requests_required_fields(
    agent_tools, session_store, agent_settings
) -> None:
    response = invoke_agent(
        AgentRequest(
            question="分析 MAT-SYN-MULTI 的根因",
            confirmed_intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
        ),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert response.status is AgentResponseStatus.NEEDS_INPUT
    assert response.missing_fields == ["warehouse_id", "as_of_date"]
    assert "物料 ID" not in response.message
    assert response.result is None


def test_two_turn_session_completes_missing_context(
    agent_tools, session_store, agent_settings
) -> None:
    first = invoke_agent(
        AgentRequest(question="分析 MAT-SYN-MULTI 的根因", session_id="session-two-turn"),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )
    second = invoke_agent(
        AgentRequest(
            question="仓库 WH-SYN-01，分析日期 2026-03-31",
            session_id="session-two-turn",
        ),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert first.status is AgentResponseStatus.NEEDS_INPUT
    assert second.status is AgentResponseStatus.OK
    assert second.parameters.material_id == "MAT-SYN-MULTI"
    assert second.parameters.warehouse_id == "WH-SYN-01"


def test_empty_risk_list_retries_once_without_category(
    agent_tools, session_store, agent_settings
) -> None:
    response = invoke_agent(
        AgentRequest(
            question="列出库存风险",
            confirmed_intent=AnalysisIntent.LIST_INVENTORY_RISKS,
            parameters=AgentParameters(
                warehouse_id="WH-SYN-01",
                category="不存在类别",
                as_of_date=date(2026, 3, 31),
            ),
        ),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert response.status is AgentResponseStatus.OK
    assert response.parameters.category is None
    assert response.action_summaries.count("empty_result_retry_without_category") == 1


class BrokenRepository:
    def get_material(self, material_id: str):
        del material_id
        raise RuntimeError("secret database stack")


def test_database_error_is_not_treated_as_empty(session_store, agent_settings) -> None:
    from app.tools import InventoryAgentTools

    response = invoke_agent(
        _request(),
        tools=InventoryAgentTools(BrokenRepository()),
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert response.status is AgentResponseStatus.ERROR
    assert response.result.metadata.status is ResultStatus.ERROR
    assert "secret" not in response.model_dump_json()


def test_empty_and_quality_blocked_results_keep_distinct_statuses(
    agent_tools, session_store, agent_settings
) -> None:
    empty_request = _request().model_copy(
        update={
            "parameters": _request().parameters.model_copy(
                update={"material_id": "MAT-SYN-EMPTY"}
            )
        }
    )
    blocked_request = _request().model_copy(
        update={
            "parameters": _request().parameters.model_copy(
                update={"material_id": "MAT-SYN-BLOCKED"}
            )
        }
    )

    empty = invoke_agent(
        empty_request,
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )
    blocked = invoke_agent(
        blocked_request,
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert empty.status is AgentResponseStatus.EMPTY
    assert blocked.status is AgentResponseStatus.BLOCKED
    assert blocked.result.metadata.status is ResultStatus.BLOCKED


class InsufficientEvidenceTools:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def analyze_material_root_cause(self, payload):
        result = self.delegate.analyze_material_root_cause(payload)
        candidates = [
            candidate.model_copy(update={"evidence": [], "insufficient_evidence": True})
            for candidate in result.root_causes
        ]
        return result.model_copy(update={"root_causes": candidates, "evidence": []})


def test_insufficient_evidence_does_not_force_root_cause_conclusion(
    agent_tools, session_store, agent_settings
) -> None:
    response = invoke_agent(
        _request(),
        tools=InsufficientEvidenceTools(agent_tools),
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert response.status is AgentResponseStatus.BLOCKED
    assert response.evidence == []
    assert "证据不足" in response.message


def test_llm_number_conflict_uses_template_result(
    agent_tools, session_store, agent_settings
) -> None:
    response = invoke_agent(
        _request(),
        tools=agent_tools,
        llm=MockLLM(
            summary=GeneratedSummary(
                summary="库存覆盖天数为 999 天。",
                suggestions=["立即处理 999 件库存"],
            )
        ),
        sessions=session_store,
        settings=agent_settings,
    )

    assert response.status is AgentResponseStatus.OK
    assert response.message == "已完成确定性分析，详见结构化指标与证据。"
    assert "llm_summary_rejected" in response.action_summaries
    assert "999" not in response.message


def test_max_execution_steps_degrades_safely(
    agent_tools, session_store, agent_settings
) -> None:
    limited = agent_settings.model_copy(update={"agent_max_steps": 3})
    response = invoke_agent(
        _request(),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=limited,
    )

    assert response.status is AgentResponseStatus.DEGRADED
    assert "最大执行步数" in response.message
    assert response.result is None


def test_same_inputs_keep_structured_result_deterministic(
    agent_tools, session_store, agent_settings
) -> None:
    first = invoke_agent(
        _request(),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )
    second = invoke_agent(
        _request(),
        tools=agent_tools,
        llm=DisabledAgentLLM(),
        sessions=session_store,
        settings=agent_settings,
    )

    assert first.result == second.result
