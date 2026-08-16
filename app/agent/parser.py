"""确定性优先的自然语言参数与意图解析。"""

import re
from datetime import date

from app.agent.llm import AgentLLM, LLMUnavailableError
from app.agent.models import AgentParameters, AnalysisIntent, ParsedRequest

_MATERIAL_PATTERN = re.compile(r"\bMAT-SYN-[A-Z0-9-]+\b", re.IGNORECASE)
_WAREHOUSE_PATTERN = re.compile(r"\bWH-SYN-[A-Z0-9-]+\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def parse_request(question: str, llm: AgentLLM) -> tuple[ParsedRequest, bool]:
    """先用可审计规则抽取；仍不完整时再尝试 LLM，且拒绝其臆造 ID。"""
    deterministic = _parse_deterministic(question)
    if _has_core_parse(deterministic) or not llm.available:
        return deterministic, False

    try:
        generated = llm.parse(question)
    except LLMUnavailableError:
        return deterministic, False

    safe = _filter_generated(question, generated)
    return _merge(deterministic, safe), True


def _parse_deterministic(question: str) -> ParsedRequest:
    material_match = _MATERIAL_PATTERN.search(question)
    warehouse_match = _WAREHOUSE_PATTERN.search(question)
    date_match = _DATE_PATTERN.search(question)
    intent = _detect_intent(question)
    parsed_date: date | None = None
    if date_match:
        try:
            parsed_date = date.fromisoformat(date_match.group())
        except ValueError:
            parsed_date = None
    return ParsedRequest(
        intent=intent,
        parameters=AgentParameters(
            material_id=material_match.group().upper() if material_match else None,
            warehouse_id=warehouse_match.group().upper() if warehouse_match else None,
            as_of_date=parsed_date,
        ),
    )


def _detect_intent(question: str) -> AnalysisIntent | None:
    lowered = question.lower()
    if any(keyword in lowered for keyword in ("证据图", "证据路径", "追溯", "trace")):
        return AnalysisIntent.TRACE_EVIDENCE
    if any(keyword in lowered for keyword in ("根因", "归因", "为什么", "原因")):
        return AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
    if any(
        keyword in lowered
        for keyword in ("风险清单", "哪些库存", "风险库存", "库存风险", "list")
    ):
        return AnalysisIntent.LIST_INVENTORY_RISKS
    if any(keyword in lowered for keyword in ("分析", "analyze")):
        return AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
    return None


def _filter_generated(question: str, generated: ParsedRequest) -> ParsedRequest:
    normalized = question.upper()
    params = generated.parameters
    material_id = (
        params.material_id
        if params.material_id and params.material_id.upper() in normalized
        else None
    )
    warehouse_id = (
        params.warehouse_id
        if params.warehouse_id and params.warehouse_id.upper() in normalized
        else None
    )
    as_of_date = (
        params.as_of_date
        if params.as_of_date and params.as_of_date.isoformat() in question
        else None
    )
    category = params.category if params.category and params.category in question else None
    return ParsedRequest(
        intent=generated.intent,
        parameters=AgentParameters(
            material_id=material_id,
            warehouse_id=warehouse_id,
            as_of_date=as_of_date,
            category=category,
        ),
    )


def _merge(primary: ParsedRequest, secondary: ParsedRequest) -> ParsedRequest:
    first = primary.parameters
    second = secondary.parameters
    return ParsedRequest(
        intent=primary.intent or secondary.intent,
        parameters=AgentParameters(
            material_id=first.material_id or second.material_id,
            warehouse_id=first.warehouse_id or second.warehouse_id,
            as_of_date=first.as_of_date or second.as_of_date,
            category=first.category or second.category,
        ),
    )


def _has_core_parse(parsed: ParsedRequest) -> bool:
    return parsed.intent is not None and parsed.parameters.as_of_date is not None
