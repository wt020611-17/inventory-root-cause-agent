"""审计日志只允许受控字段，并对疑似凭据二次脱敏。"""

import io
import json
import logging

from app.core.audit_logging import AUDIT_LOGGER_NAME, AuditJsonFormatter, audit_event


def test_audit_event_omits_input_prompt_and_private_reasoning() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AuditJsonFormatter())
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    original_handlers = logger.handlers[:]
    original_level = logger.level
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    try:
        audit_event(
            "agent_tool_completed",
            trace_id="trace-safe",
            tool_name="analyze_material_root_cause",
            result_status="ok",
            elapsed_ms=12.5,
            question="完整用户问题不应出现",
            api_key="sk-" + "a" * 24,
            chain_of_thought="private reasoning",
        )
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)

    payload = json.loads(stream.getvalue())
    assert payload["trace_id"] == "trace-safe"
    assert payload["tool_name"] == "analyze_material_root_cause"
    assert payload["result_status"] == "ok"
    assert "question" not in payload
    assert "api_key" not in payload
    assert "chain_of_thought" not in payload
    assert "private reasoning" not in stream.getvalue()


def test_trace_id_with_bearer_token_is_redacted() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AuditJsonFormatter())
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    original_handlers = logger.handlers[:]
    original_level = logger.level
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    try:
        audit_event("api_request_completed", trace_id="Bearer " + "x" * 20)
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)

    assert json.loads(stream.getvalue())["trace_id"] == "[REDACTED]"
