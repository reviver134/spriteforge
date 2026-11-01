import re
import shutil
import subprocess
from datetime import date
from pathlib import PurePosixPath

import yaml

from spriteforge.core.security import redact

from .codec import ensure_no_secrets
from .models import ValidationReport
from .store import KnowledgeStore


def validate(store: KnowledgeStore, external: bool = True) -> ValidationReport:
    entries, errors = store.scan()
    report = ValidationReport(errors=errors)
    for entry in entries:
        if entry.metadata.stale:
            report.warnings.append(f"{entry.id}: stale since {entry.metadata.stale_after}")
        if entry.metadata.type == "Attested Computation" and not (
            entry.metadata.model_extra or {}
        ).get("runtime"):
            report.warnings.append(f"{entry.id}: Attested Computation should declare runtime")
    for path in store.root.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(store.root).parts):
            continue
        if path.name not in {"index.md", "log.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            ensure_no_secrets(text)
            body = text
            if text.startswith("---\n"):
                match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
                if not match or path != store.root / "index.md":
                    raise ValueError("Only root index may carry version frontmatter")
                data = yaml.safe_load(match[1])
                if not isinstance(data, dict) or set(data) != {"okf_version"}:
                    raise ValueError("Only okf_version is defined on root index")
                body = text[match.end() :]
            if not re.search(r"^#{1,6} .+", body, re.MULTILINE):
                raise ValueError("Reserved file must have a section heading")
            if path.name == "log.md":
                headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
                if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", heading) for heading in headings):
                    raise ValueError("Log date headings must be YYYY-MM-DD")
                for heading in headings:
                    date.fromisoformat(heading)
        except (ValueError, yaml.YAMLError):
            report.errors.append(f"{path.relative_to(store.root)}: invalid reserved-file structure")
    if store.bundle:
        report.errors += bundle_problems(store, entries)
    command = external_validator(store.root) if external else None
    if command:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        report.external = (
            redact(result.stdout + result.stderr).strip() or f"exit {result.returncode}"
        )
        if result.returncode:
            report.errors.append("External okf validate failed")
    return report


# Markdown links and images: [text](target) / ![alt](target). Code spans are left out first.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#)", re.IGNORECASE)


def links(text: str) -> list[str]:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    return LINK.findall(re.sub(r"`[^`\n]*`", " ", text))


def resolves(store: KnowledgeStore, target: str) -> bool:
    path = PurePosixPath(target.split("#", 1)[0])
    if any(p in {".", ".."} for p in path.parts):
        return False
    file = (store.root / str(path).lstrip("/")).resolve()
    return file.is_relative_to(store.root) and file.is_file()


def bundle_problems(store: KnowledgeStore, entries) -> list[str]:
    """A self-contained bundle (a game's record): every link in concepts and indexes is
    absolute (`/entities/hero.md`) and resolves; every `resource` path exists."""
    problems = []
    texts = {f"{e.id}.md": e.body for e in entries}
    for path in store.root.rglob("index.md"):
        relative = path.relative_to(store.root)
        if not any(part.startswith(".") for part in relative.parts):
            texts[relative.as_posix()] = path.read_text(encoding="utf-8")
    for name, text in sorted(texts.items()):
        for target in links(text):
            if EXTERNAL.match(target):
                continue
            if not target.startswith("/"):
                problems.append(f"{name}: link {target} is not absolute")
            elif not resolves(store, target):
                problems.append(f"{name}: link {target} does not resolve")
    for e in entries:
        resource = e.metadata.resource
        if resource and not EXTERNAL.match(resource) and not resolves(store, resource):
            problems.append(f"{e.id}: resource {resource} does not exist")
    return problems


def external_validator(root) -> list[str] | None:
    """The command of an installed external OKF validator: `okf validate <dir>`, or okflib's
    `okflib -C <dir> validate` (PyPI `okflib`); None when neither is installed."""
    if binary := shutil.which("okf"):
        return [binary, "validate", str(root)]
    if binary := shutil.which("okflib"):
        return [binary, "-C", str(root), "validate"]
    return None
