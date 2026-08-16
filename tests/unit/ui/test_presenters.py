"""UI 展示转换必须保留后端事实、排序和安全边界。"""

from datetime import date

import pytest

from app.agent import (
    AgentParameters,
    AgentRequest,
    AgentResponseStatus,
    AnalysisIntent,
)
from app.ui.presenters import (
    evidence_path_rows,
    evidence_rows,
    llm_mode_label,
    metric_cards,
    root_cause_rows,
    safe_action_summaries,
)
from app.ui.runtime import create_ui_runtime, invoke_ui_agent


@pytest.fixture(scope="module")
def runtime():
    value = create_ui_runtime()
    yield value
    value.close()


def invoke(runtime, material_id: str, intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE):
    return invoke_ui_agent(
        runtime,
        AgentRequest(
            question="演示查询",
            confirmed_intent=intent,
            parameters=AgentParameters(
                material_id=material_id,
                warehouse_id="WH-SYN-01",
                as_of_date=date(2026, 3, 31),
            ),
            session_id=f"ui-{material_id}-{intent.value}",
        ),
        disable_llm=True,
    )


def test_multi_cause_preserves_backend_order_and_evidence(runtime) -> None:
    response = invoke(runtime, "MAT-SYN-MULTI")
    rows = root_cause_rows(response)

    assert response.status is AgentResponseStatus.OK
    assert [row["候选根因"] for row in rows[:2]] == ["超量采购", "生产延期"]
    assert [row["排序"] for row in rows] == list(range(1, len(rows) + 1))
    assert len(metric_cards(response)) == 4
    assert evidence_rows(response)
    assert llm_mode_label(response) == "确定性模板降级"


def test_normal_empty_and_blocked_states_remain_distinct(runtime) -> None:
    normal = invoke(runtime, "MAT-SYN-NORMAL")
    empty = invoke(runtime, "MAT-SYN-EMPTY")
    blocked = invoke(runtime, "MAT-SYN-BLOCKED")

    assert normal.status is AgentResponseStatus.OK
    assert empty.status is AgentResponseStatus.EMPTY
    assert blocked.status is AgentResponseStatus.BLOCKED
    assert root_cause_rows(blocked) == []


def test_evidence_trace_exposes_paths_without_private_reasoning(runtime) -> None:
    response = invoke(runtime, "MAT-SYN-MULTI", AnalysisIntent.TRACE_EVIDENCE)
    actions = safe_action_summaries(response)

    assert response.status is AgentResponseStatus.OK
    assert evidence_path_rows(response)
    assert any(action == "tool_executed:trace_evidence" for action in actions)
    assert "thought" not in " ".join(actions).lower()
    assert "prompt" not in " ".join(actions).lower()
