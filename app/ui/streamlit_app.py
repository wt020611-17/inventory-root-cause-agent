"""呆滞料智能体的本地 Streamlit 演示入口。"""

from datetime import date
from uuid import uuid4

import streamlit as st

from app.agent import AgentParameters, AgentRequest, AgentResponse, AnalysisIntent
from app.ui.presenters import (
    evidence_path_rows,
    evidence_rows,
    llm_mode_label,
    metric_cards,
    risk_list_rows,
    risk_summary,
    root_cause_rows,
    safe_action_summaries,
    status_label,
)
from app.ui.runtime import UIRuntime, create_ui_runtime, invoke_ui_agent, list_demo_options


@st.cache_resource
def _runtime() -> UIRuntime:
    return create_ui_runtime()


def _initialize_state() -> None:
    st.session_state.setdefault("session_id", uuid4().hex)
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("last_response", None)


def _apply_style() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #f7f7f3; color: #1f2a25; }
        .block-container { max-width: 1180px; padding-top: 2rem; }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e2e5dd;
            border-radius: 14px;
            padding: 1rem;
            box-shadow: 0 8px 26px rgba(44, 57, 49, 0.05);
        }
        [data-testid="stSidebar"] { background: #eef1e9; }
        .synthetic-banner {
            display: inline-block;
            padding: 0.35rem 0.7rem;
            border-radius: 999px;
            color: #315b45;
            background: #dfeadf;
            font-size: 0.8rem;
            font-weight: 700;
            letter-spacing: 0.04em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _run_filtered_request(
    runtime: UIRuntime,
    *,
    intent: AnalysisIntent,
    material_id: str,
    warehouse_id: str,
    as_of_date: date,
    disable_llm: bool,
) -> AgentResponse:
    labels = {
        AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE: "分析库存根因",
        AnalysisIntent.TRACE_EVIDENCE: "追溯证据路径",
    }
    return invoke_ui_agent(
        runtime,
        AgentRequest(
            question=labels[intent],
            confirmed_intent=intent,
            parameters=AgentParameters(
                material_id=material_id,
                warehouse_id=warehouse_id,
                as_of_date=as_of_date,
            ),
            session_id=st.session_state.session_id,
        ),
        disable_llm=disable_llm,
    )


def _render_status(response: AgentResponse) -> None:
    message = f"**{status_label(response)}** · {response.message}"
    renderer = {
        "ok": st.success,
        "empty": st.info,
        "blocked": st.warning,
        "error": st.error,
        "needs_input": st.warning,
        "degraded": st.warning,
    }[response.status.value]
    renderer(message)
    st.caption(
        f"状态：{response.status.value} · 摘要模式：{llm_mode_label(response)} · "
        f"Trace ID：{response.trace_id}"
    )
    if not response.llm_used and response.status.value == "ok":
        st.info("LLM 未启用或不可用：已使用确定性结果与受控模板完成展示。")


def _render_response(response: AgentResponse) -> None:
    _render_status(response)
    summary = risk_summary(response)
    if summary:
        label, matched_rules = summary
        st.subheader(f"风险判断 · {label}")
        if matched_rules:
            st.caption("命中规则：" + "；".join(matched_rules))

    cards = metric_cards(response)
    if cards:
        columns = st.columns(len(cards))
        for column, (label, value, help_text) in zip(columns, cards, strict=True):
            column.metric(label, value, help=help_text)

    causes = root_cause_rows(response)
    if causes:
        st.subheader("候选根因")
        st.dataframe(causes, use_container_width=True, hide_index=True)

    risks = risk_list_rows(response)
    if risks:
        st.subheader("库存风险清单")
        st.dataframe(risks, use_container_width=True, hide_index=True)

    paths = evidence_path_rows(response)
    if paths:
        st.subheader("关系路径")
        st.dataframe(paths, use_container_width=True, hide_index=True)

    evidence = evidence_rows(response)
    if evidence:
        with st.expander(f"证据单据与结构化事实（{len(evidence)}）", expanded=True):
            st.dataframe(evidence, use_container_width=True, hide_index=True)


def _render_sidebar(response: AgentResponse | None, *, disable_llm: bool) -> None:
    with st.sidebar:
        st.header("运行摘要")
        st.caption("仅展示工具与动作摘要，不展示模型私有推理。")
        st.write("数据源：固定 seed 纯合成数据")
        st.write("模型模式：" + ("强制无 LLM" if disable_llm else "按本地配置"))
        if response is None:
            st.info("运行一次分析后，这里会显示受控动作。")
            return
        st.write("选用工具：", response.selected_tool or "尚未选择")
        for action in safe_action_summaries(response):
            st.code(action, language=None)


def main() -> None:
    st.set_page_config(
        page_title="呆滞料智能体",
        page_icon="📦",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _initialize_state()
    _apply_style()
    runtime = _runtime()
    materials, warehouses = list_demo_options(runtime)

    st.markdown(
        '<span class="synthetic-banner">SYNTHETIC DATA · 仅使用合成数据</span>',
        unsafe_allow_html=True,
    )
    st.title("呆滞料智能体")
    st.write("用确定性指标、规则归因和受限证据路径，解释库存为什么形成风险。")

    with st.sidebar:
        st.header("分析条件")
        material_id = st.selectbox(
            "物料",
            options=[item[0] for item in materials],
            index=next(
                (index for index, item in enumerate(materials) if item[0] == "MAT-SYN-MULTI"),
                0,
            ),
            format_func=lambda value: next(
                f"{material_id} · {name}"
                for material_id, name in materials
                if material_id == value
            ),
        )
        warehouse_id = st.selectbox(
            "仓库",
            options=[item[0] for item in warehouses],
            format_func=lambda value: next(
                f"{warehouse_id} · {name}"
                for warehouse_id, name in warehouses
                if warehouse_id == value
            ),
        )
        as_of_date = st.date_input("分析日期", value=date(2026, 3, 31))
        disable_llm = st.toggle("无 LLM 演示模式", value=True)

    analysis_tab, chat_tab = st.tabs(["分析工作台", "连续对话"])
    with analysis_tab:
        left, right = st.columns(2)
        analyze_clicked = left.button(
            "运行根因分析",
            type="primary",
            use_container_width=True,
        )
        trace_clicked = right.button("追溯证据路径", use_container_width=True)
        if analyze_clicked or trace_clicked:
            intent = (
                AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
                if analyze_clicked
                else AnalysisIntent.TRACE_EVIDENCE
            )
            st.session_state.last_response = _run_filtered_request(
                runtime,
                intent=intent,
                material_id=material_id,
                warehouse_id=warehouse_id,
                as_of_date=as_of_date,
                disable_llm=disable_llm,
            )
        if st.session_state.last_response is None:
            st.info("选择条件后运行分析。建议先体验多根因物料 MAT-SYN-MULTI。")
        else:
            _render_response(st.session_state.last_response)

    with chat_tab:
        for entry in st.session_state.chat_history:
            with st.chat_message("user"):
                st.write(entry["question"])
            with st.chat_message("assistant"):
                st.write(entry["response"].message)
                st.caption(
                    f"{entry['response'].status.value} · "
                    f"{entry['response'].selected_tool or '等待补参'}"
                )
        question = st.chat_input(
            "例如：分析 MAT-SYN-MULTI 在 WH-SYN-01 截至 2026-03-31 的根因"
        )
        if question:
            response = invoke_ui_agent(
                runtime,
                AgentRequest(
                    question=question,
                    session_id=st.session_state.session_id,
                ),
                disable_llm=disable_llm,
            )
            st.session_state.chat_history.append({"question": question, "response": response})
            st.session_state.last_response = response
            st.rerun()

    _render_sidebar(st.session_state.last_response, disable_llm=disable_llm)


if __name__ == "__main__":
    main()
