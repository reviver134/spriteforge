import re
import shutil
import subprocess
from datetime import date

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
    if external and (binary := shutil.which("okf")):
        result = subprocess.run(
            [binary, "validate", str(store.root)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        report.external = (
            redact(result.stdout + result.stderr).strip() or f"exit {result.returncode}"
        )
        if result.returncode:
            report.errors.append("External okf validate failed")
    return report
