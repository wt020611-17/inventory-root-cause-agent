"""应用级横切能力。"""

from app.core.audit_logging import audit_event, configure_audit_logging

__all__ = ["audit_event", "configure_audit_logging"]
