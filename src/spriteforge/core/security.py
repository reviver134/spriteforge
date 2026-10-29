"""Scrub credentials at persistence and error boundaries, including echoed API errors."""

import os
import re
from typing import Any

USAGE_COUNTERS = {
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "known_input_tokens",
    "known_output_tokens",
    "known_cached_input_tokens",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.search(r"key|token|authorization|secret", k, re.IGNORECASE)
            and not (k in USAGE_COUNTERS and (v is None or (type(v) is int and v >= 0)))
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
