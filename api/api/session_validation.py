from __future__ import annotations

import logging
import sqlite3
from typing import Any

from flask import jsonify


logger = logging.getLogger(__name__)


def coerce_sqlite_text_param(value: Any, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8", errors="replace")
    elif not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


def build_sqlite_param_error_payload(
    exc: Exception,
    *,
    field_name: str,
    route_name: str,
    raw_value: Any,
) -> dict[str, Any]:
    text_value = raw_value if isinstance(raw_value, str) else str(raw_value)
    return {
        "error": f"invalid {field_name} parameter",
        "detail": {
            "route": route_name,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "value_type": type(raw_value).__name__,
            "value_length": len(text_value),
            "value_preview": text_value[:120],
            "sqlite_error_name": getattr(exc, "sqlite_errorname", None),
            "sqlite_error_code": getattr(exc, "sqlite_errorcode", None),
        },
    }


def validate_session_route_scope(app, session_id: Any, *, route_name: str):
    try:
        normalized = coerce_sqlite_text_param(session_id, "session_id")
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 400)

    try:
        row = app.db.execute("SELECT id FROM sessions WHERE id = ?", (normalized,)).fetchone()
    except sqlite3.Error as exc:
        logger.exception(
            "session route validation failed",
            extra={
                "route": route_name,
                "session_id_preview": normalized[:120],
                "session_id_length": len(normalized),
                "sqlite_error_name": getattr(exc, "sqlite_errorname", None),
                "sqlite_error_code": getattr(exc, "sqlite_errorcode", None),
            },
        )
        return None, (
            jsonify(
                build_sqlite_param_error_payload(
                    exc,
                    field_name="session_id",
                    route_name=route_name,
                    raw_value=normalized,
                )
            ),
            400,
        )

    if not row:
        return None, (jsonify({"error": "session not found"}), 404)

    return normalized, None