"""Persistent, searchable audit log for the C-Suite system.

Captures valuable, non-verbose events (chat turns, specialist consultations,
tool invocations, scheduled actions, alerts, inbound integration events)
into a SQLite table for later review and search.
"""
from csuite.audit.context import (
    bind_turn,
    clear_turn,
    get_active_ids,
    get_active_session_id,
    get_active_turn_id,
    set_turn,
)
from csuite.audit.logger import (
    AuditEvent,
    AuditLogger,
    get_audit_logger,
    log_event,
    set_audit_logger,
)

__all__ = [
    "AuditEvent",
    "AuditLogger",
    "bind_turn",
    "clear_turn",
    "get_active_ids",
    "get_active_session_id",
    "get_active_turn_id",
    "get_audit_logger",
    "log_event",
    "set_audit_logger",
    "set_turn",
]
