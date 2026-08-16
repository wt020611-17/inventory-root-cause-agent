"""输入解析优先级与防幻觉测试。"""

from datetime import date

from app.agent.models import AgentParameters, AnalysisIntent, ParsedRequest
from app.agent.parser import parse_request


class ParserLLM:
    available = True

    def __init__(self, parsed: ParsedRequest) -> None:
        self.parsed = parsed

    def parse(self, question: str) -> ParsedRequest:
        del question
        return self.parsed

    def summarize(self, result_json: str):
        raise AssertionError(result_json)


def test_deterministic_parser_extracts_ids_date_and_intent() -> None:
    parsed, llm_used = parse_request(
        "请分析 MAT-SYN-MULTI 在 WH-SYN-01 截至 2026-03-31 的根因",
        ParserLLM(ParsedRequest()),
    )

    assert parsed.intent is AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
    assert parsed.parameters.material_id == "MAT-SYN-MULTI"
    assert parsed.parameters.warehouse_id == "WH-SYN-01"
    assert parsed.parameters.as_of_date == date(2026, 3, 31)
    assert llm_used is False


def test_llm_cannot_invent_ids_or_dates_not_in_question() -> None:
    parsed, llm_used = parse_request(
        "帮我做根因分析",
        ParserLLM(
            ParsedRequest(
                intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
                parameters=AgentParameters(
                    material_id="MAT-SYN-HALLUCINATED",
                    warehouse_id="WH-SYN-99",
                    as_of_date=date(2030, 1, 1),
                ),
            )
        ),
    )

    assert parsed.intent is AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
    assert parsed.parameters == AgentParameters()
    assert llm_used is True
