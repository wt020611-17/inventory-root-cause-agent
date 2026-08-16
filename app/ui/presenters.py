"""把严格领域结果转换为只读 UI 行，不在展示层重算业务结论。"""

import json
from decimal import Decimal

from app.agent import AgentResponse, AgentResponseStatus
from app.domain.enums import InventoryRiskLevel, RootCauseType
from app.domain.results import AnalysisResult, EvidenceGraphResult, RiskListResult

_RISK_LABELS = {
    InventoryRiskLevel.NORMAL: "正常",
    InventoryRiskLevel.SLOW_MOVING: "慢动",
    InventoryRiskLevel.NON_MOVING: "无动",
    InventoryRiskLevel.DATA_QUALITY_BLOCKED: "数据质量阻断",
}
_CAUSE_LABELS = {
    RootCauseType.DEMAND_DROP: "需求下降",
    RootCauseType.PURCHASE_EXCESS: "超量采购",
    RootCauseType.PRODUCTION_DELAY: "生产延期",
}
_STATUS_LABELS = {
    AgentResponseStatus.OK: "分析完成",
    AgentResponseStatus.EMPTY: "没有匹配数据",
    AgentResponseStatus.BLOCKED: "数据质量阻断",
    AgentResponseStatus.ERROR: "执行失败",
    AgentResponseStatus.NEEDS_INPUT: "需要补充参数",
    AgentResponseStatus.DEGRADED: "已安全降级",
}
_SAFE_ACTIONS = {
    "request_initialized",
    "input_parsed",
    "required_input_requested",
    "tool_execution_failed",
    "evidence_validated",
    "evidence_rejected",
    "empty_result_retry_without_category",
    "template_summary_selected",
    "llm_summary_accepted",
    "llm_summary_rejected",
    "llm_summary_unavailable",
    "response_organized",
    "execution_limit_reached",
}
_SAFE_ACTION_PREFIXES = ("tool_selected:", "tool_executed:")


def status_label(response: AgentResponse) -> str:
    return _STATUS_LABELS[response.status]


def metric_cards(response: AgentResponse) -> list[tuple[str, str, str]]:
    """返回四个核心指标；数值直接来自 AnalysisResult。"""
    result = response.result
    if not isinstance(result, AnalysisResult) or result.metrics is None:
        return []
    metrics = result.metrics
    coverage = (
        "∞"
        if metrics.infinite_coverage
        else _format_value(metrics.coverage_days.value, metrics.coverage_days.unit)
    )
    return [
        (
            "当前库存",
            _format_value(metrics.current_stock.value, metrics.current_stock.unit),
            "截至分析日期的流水聚合库存",
        ),
        (
            "无消耗天数",
            _format_value(
                metrics.days_without_consumption.value,
                metrics.days_without_consumption.unit,
            ),
            "仅销售出库与生产领料计为有效消耗",
        ),
        ("库存覆盖", coverage, "平均日消耗为零时以 ∞ 表示"),
        (
            "呆滞金额",
            _format_value(metrics.stagnant_amount.value, metrics.stagnant_amount.unit),
            "呆滞数量 × 合成标准成本",
        ),
    ]


def risk_summary(response: AgentResponse) -> tuple[str, list[str]] | None:
    result = response.result
    if not isinstance(result, AnalysisResult) or result.risk is None:
        return None
    return _RISK_LABELS[result.risk.risk_level], result.risk.matched_rules


def root_cause_rows(response: AgentResponse) -> list[dict[str, object]]:
    """保持服务层排序，只添加展示序号和中文标签。"""
    result = response.result
    if not isinstance(result, AnalysisResult):
        return []
    return [
        {
            "排序": rank,
            "候选根因": _CAUSE_LABELS[candidate.cause_type],
            "确定性得分": f"{candidate.score:.0%}",
            "证据状态": "证据不足" if candidate.insufficient_evidence else "已核验",
            "规则命中": "；".join(candidate.hits),
        }
        for rank, candidate in enumerate(result.root_causes, start=1)
    ]


def evidence_rows(response: AgentResponse) -> list[dict[str, str]]:
    """将证据压平成表格行；不展示提示词或模型私有推理。"""
    rows: list[dict[str, str]] = []
    for item in response.evidence:
        source_type = str(item.get("source_type") or item.get("kind") or "evidence")
        source_id = str(
            item.get("source_id") or item.get("node_id") or item.get("source_node_id") or "-"
        )
        summary = str(item.get("summary") or _graph_summary(item))
        facts = item.get("facts")
        rows.append(
            {
                "来源类型": source_type,
                "来源标识": source_id,
                "事实摘要": summary,
                "结构化事实": _json_text(facts) if facts else "-",
            }
        )
    return rows


def evidence_path_rows(response: AgentResponse) -> list[dict[str, str | int]]:
    result = response.result
    if not isinstance(result, EvidenceGraphResult):
        return []
    return [
        {
            "路径": index,
            "节点": " → ".join(path.node_ids),
            "关系": " → ".join(item.value for item in path.relation_types),
        }
        for index, path in enumerate(result.paths, start=1)
    ]


def risk_list_rows(response: AgentResponse) -> list[dict[str, str]]:
    result = response.result
    if not isinstance(result, RiskListResult):
        return []
    rows = []
    for item in result.items:
        if item.metrics is None or item.risk is None:
            continue
        rows.append(
            {
                "物料": item.metrics.material_id,
                "仓库": item.metrics.warehouse_id,
                "风险": _RISK_LABELS[item.risk.risk_level],
                "当前库存": _format_value(
                    item.metrics.current_stock.value,
                    item.metrics.current_stock.unit,
                ),
                "呆滞金额": _format_value(
                    item.metrics.stagnant_amount.value,
                    item.metrics.stagnant_amount.unit,
                ),
            }
        )
    return rows


def safe_action_summaries(response: AgentResponse) -> list[str]:
    """二次白名单过滤，确保侧栏只展示动作摘要。"""
    return [
        action
        for action in response.action_summaries
        if action in _SAFE_ACTIONS or action.startswith(_SAFE_ACTION_PREFIXES)
    ]


def llm_mode_label(response: AgentResponse) -> str:
    return "LLM 摘要" if response.llm_used else "确定性模板降级"


def _format_value(value: Decimal | None, unit: str) -> str:
    if value is None:
        return "—"
    normalized = format(value, "f").rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        normalized = "0"
    unit_label = {"unit": "件", "day": "天", "CNY": "元"}.get(unit, unit)
    return f"{normalized} {unit_label}"


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _graph_summary(item: dict[str, object]) -> str:
    kind = item.get("kind")
    if kind == "node":
        return f"合成业务节点：{item.get('node_type', '-')}"
    if kind == "edge":
        return (
            f"{item.get('source_node_id', '-')} --{item.get('relation_type', '-')}--> "
            f"{item.get('target_node_id', '-')}"
        )
    if kind == "path":
        return "受限证据路径"
    return "结构化证据"
