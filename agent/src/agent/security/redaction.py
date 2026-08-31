"""PII redaction for anything leaving the process boundary as telemetry.

`customer_id` and `service_id` are treated as internal correlation
identifiers, not direct PII, and are kept -- without them, LangSmith traces,
logs, and CloudWatch/Datadog/Splunk records could not be correlated to a
support case at all. Names, phone numbers, emails, and addresses are always
masked before they reach LangSmith metadata or structured logs. See
docs/security.md for the full rationale.
"""

from __future__ import annotations

import re
from typing import Any

_PII_KEYS = {"name", "customer_name", "phone", "phone_number", "email", "address"}
_REDACTED = "***REDACTED***"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\-\s]{7,}\d)\b")


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_dict(data: dict) -> dict:
    return {
        key: (_REDACTED if key.lower() in _PII_KEYS else redact_value(value))
        for key, value in data.items()
    }


def redact_text(text: str) -> str:
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _PHONE_RE.sub(_REDACTED, text)
    return text
