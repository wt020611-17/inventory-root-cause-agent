"""只允许受控字段的 JSON 审计日志，避免记录输入正文和私有推理。"""

import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import TextIO

AUDIT_LOGGER_NAME = "inventory_agent.audit"
_ALLOWED_FIELDS = {
    "method",
    "route",
    "http_status",
    "elapsed_ms",
    "tool_name",
    "result_status",
    "error_category",
    "retry_count",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[a-zA-Z0-9_-]{12,}"),
)


class AuditJsonFormatter(logging.Formatter):
    """把受控 LogRecord 转换成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "audit_event", "application_event"),
            "trace_id": getattr(record, "trace_id", "unavailable"),
        }
        fields = getattr(record, "audit_fields", {})
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_audit_logging(
    *,
    level: str | None = None,
    stream: TextIO | None = None,
) -> logging.Logger:
    """幂等配置独立审计 logger，不接管第三方库日志。"""
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.setLevel((level or os.getenv("APP_LOG_LEVEL", "INFO")).upper())
    logger.propagate = False
    if not any(getattr(handler, "_inventory_audit", False) for handler in logger.handlers):
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(AuditJsonFormatter())
        handler._inventory_audit = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def audit_event(
    event: str,
    *,
    trace_id: str,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """记录白名单字段；问题正文、提示词、密钥等任意额外字段会被丢弃。"""
    safe_fields = {
        key: _sanitize_value(value)
        for key, value in fields.items()
        if key in _ALLOWED_FIELDS and value is not None
    }
    logging.getLogger(AUDIT_LOGGER_NAME).log(
        level,
        event,
        extra={
            "audit_event": event,
            "trace_id": _sanitize_value(trace_id),
            "audit_fields": safe_fields,
        },
    )


def _sanitize_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
        return "[REDACTED]"
    return value[:256]
