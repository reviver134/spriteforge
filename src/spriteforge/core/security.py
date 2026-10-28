"""Scrub credentials at persistence and error boundaries, including echoed API errors."""

import os
import re
from typing import Any


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.search(r"key|token|authorization|secret", k, re.IGNORECASE)
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for name, secret in os.environ.items():
            if secret and re.search(r"API_KEY|TOKEN|SECRET|PASSWORD", name, re.IGNORECASE):
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value, flags=re.IGNORECASE)
    return value
