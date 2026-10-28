import re
from pathlib import PurePosixPath

import yaml

from spriteforge.core.security import redact

from .models import Entry, Metadata


class UnsafeKnowledge(ValueError):
    pass


def ensure_no_secrets(text: str) -> None:
    assignment = r"(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"']?[^\s\"'`]+"
    if redact(text) != text or re.search(assignment, text) or "-----BEGIN PRIVATE KEY" in text:
        raise UnsafeKnowledge("Credential-like content rejected; no knowledge was persisted")


def check_id(ident: str) -> str:
    path = PurePosixPath(ident)
    if (
        not ident
        or "\\" in ident
        or ":" in ident
        or path.is_absolute()
        or any(p in {".", ".."} or p.startswith(".") for p in path.parts)
        or str(path) != ident
        or path.name in {"index", "log"}
        or ident.endswith(".md")
    ):
        raise ValueError("Concept ID must be a safe relative path without .md or reserved names")
    ensure_no_secrets(ident)
    return ident


def parse(text: str, ident: str) -> Entry:
    check_id(ident)
    ensure_no_secrets(text)
    text = text.lstrip("\ufeff")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if match is None:
        raise ValueError("Missing YAML frontmatter")
    data = yaml.safe_load(match[1])
    if not isinstance(data, dict):
        raise ValueError("Frontmatter must be a mapping")  # noqa: TRY004 -- serialized YAML format error
    return Entry(id=ident, metadata=Metadata.model_validate(data), body=text[match.end() :])


def serialize(entry: Entry) -> str:
    check_id(entry.id)
    metadata = entry.metadata.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if entry.private:
        metadata["x-spriteforge-scope"] = "private"
    result = (
        "---\n"
        + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
        + "---\n"
        + entry.body
    )
    ensure_no_secrets(result)
    return result
