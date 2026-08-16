"""API、Tool 日志、追踪号与稳定错误响应集成测试。"""

import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from starlette.testclient import TestClient

from app.core.audit_logging import AUDIT_LOGGER_NAME, AuditJsonFormatter
from app.main import create_app


@contextmanager
def captured_audit_events() -> Iterator[list[dict[str, object]]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AuditJsonFormatter())
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    original_handlers = logger.handlers[:]
    original_level = logger.level
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    events: list[dict[str, object]] = []
    try:
        yield events
    finally:
        events.extend(json.loads(line) for line in stream.getvalue().splitlines())
        logger.handlers = original_handlers
        logger.setLevel(original_level)


def test_chat_logs_trace_duration_tool_and_status_without_full_input() -> None:
    secret_marker = "Bearer " + "x" * 20
    app = create_app(database_url="sqlite+pysqlite:///:memory:", seed=7)
    with captured_audit_events() as events:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat",
                json={
                    "question": (
                        "分析 MAT-SYN-MULTI 在 WH-SYN-01 截至 2026-03-31 的根因 " + secret_marker
                    )
                },
            )

    assert response.headers["X-Trace-ID"] == response.json()["trace_id"]
    api_event = next(item for item in events if item["event"] == "api_request_completed")
    tool_event = next(item for item in events if item["event"] == "agent_tool_completed")
    assert api_event["trace_id"] == response.json()["trace_id"]
    assert api_event["tool_name"] == "analyze_material_root_cause"
    assert api_event["result_status"] == "ok"
    assert api_event["elapsed_ms"] >= 0
    assert tool_event["tool_name"] == "analyze_material_root_cause"
    assert secret_marker not in json.dumps(events)
    assert "question" not in json.dumps(events)


def test_invalid_request_has_stable_error_and_observable_category() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:", seed=7)
    with captured_audit_events() as events:
        with TestClient(app) as client:
            response = client.post("/api/v1/analysis", json={"unknown": "field"})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.headers["X-Trace-ID"] == response.json()["trace_id"]
    event = next(item for item in events if item["event"] == "api_request_completed")
    assert event["error_category"] == "invalid_request"
    assert event["result_status"] == "error"


def test_unhandled_exception_returns_stable_error_without_details() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:", seed=7)

    @app.get("/test-only-unhandled")
    def raise_unhandled() -> None:
        raise RuntimeError("secret database stack")

    with captured_audit_events() as events:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/test-only-unhandled")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret database stack" not in response.text
    assert response.json()["trace_id"]
    failed = next(item for item in events if item["event"] == "api_request_failed")
    assert failed["error_category"] == "unhandled_exception"
