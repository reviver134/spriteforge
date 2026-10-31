"""Filesystem OKF corpus with Git write batches and bounded, event-logged retrieval."""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from spriteforge.config import MemoryConfig, load_config
from spriteforge.core.state import Store as EventStore

from . import git
from .codec import check_id, ensure_no_secrets, parse, serialize
from .indexes import indexes
from .models import Entry, Metadata, Snippet
from .search import rank, sentence_excerpt, token_cost

CATEGORIES = {
    "genres",
    "art-styles",
    "engine-patterns",
    "prompt-patterns",
    "lessons",
    "builds",
    "user-prefs",
    "catalog",  # asset packs, tile sets and actors mirrored by `memory sync-catalog`
}


class KnowledgeStore:
    def __init__(self, root: Path, events: EventStore | None = None, run_id: str | None = None):
        self.root = root.expanduser().resolve()
        git.init(self.root)
        self.events = events or EventStore(self.root.parent / "state.sqlite3")
        if run_id is None:
            prior = [
                r
                for r in self.events.runs()
                if r.config.get("workflow") == "memory-audit"
                and r.config.get("corpus") == str(self.root)
            ]
            run_id = (
                prior[0].id
                if prior
                else self.events.create_run(
                    {"workflow": "memory-audit", "corpus": str(self.root)}
                ).id
            )
        self.run_id = run_id

    def path(self, ident: str) -> Path:
        check_id(ident)
        path = (self.root / (ident + ".md")).resolve()
        if not path.is_relative_to(self.root) or any(
            p.startswith(".") for p in path.relative_to(self.root).parts
        ):
            raise ValueError("Concept path escapes corpus")
        return path

    def scan(self) -> tuple[list[Entry], list[str]]:
        entries, errors = [], []
        for path in sorted(self.root.rglob("*.md")):
            relative = path.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts) or path.name in {
                "index.md",
                "log.md",
            }:
                continue
            try:
                if not path.resolve().is_relative_to(self.root):
                    raise ValueError("External symlink")
                entries.append(
                    parse(path.read_text(encoding="utf-8"), relative.with_suffix("").as_posix())
                )
            except (ValueError, OSError, ValidationError, yaml.YAMLError):
                errors.append(f"{relative.as_posix()}: invalid or unsafe concept; inspect locally")
        return entries, errors

    def _log(self, level: str, query: str, scopes: list[str], results: list[dict]) -> None:
        self.events.append_event(
            self.run_id,
            "Librarian",
            "memory.retrieved",
            {
                "level": level,
                "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                "scopes": scopes,
                "results": results,
            },
        )

    @staticmethod
    def permitted(entry: Entry, scopes: list[str]) -> bool:
        return ("private" if entry.private else "project") in scopes

    def retrieve(
        self,
        query: str,
        scopes: list[str] | None = None,
        token_budget: int = 1024,
        *,
        min_score: float | None = None,
        relative_score: float | None = None,
    ) -> list[Snippet]:
        scopes = ["project"] if scopes is None else scopes
        if set(scopes) - {"project", "private"} or token_budget < 0:
            raise ValueError("Invalid scopes or token budget")
        ensure_no_secrets(query)
        entries, _ = (
            self.scan()
        )  # Refresh on every request: drop-in genre files need no code changes.
        candidates = [entry for entry in entries if self.permitted(entry, scopes)]
        snippets = []
        remaining = token_budget
        defaults = load_config().memory
        cutoff = MemoryConfig(
            min_score=defaults.min_score if min_score is None else min_score,
            relative_score=defaults.relative_score if relative_score is None else relative_score,
        )
        ranked = rank(query, candidates)
        threshold = max(cutoff.min_score, ranked[0][1] * cutoff.relative_score) if ranked else 0
        for entry, score in ranked:
            if score < threshold:
                continue
            header = f"[{entry.metadata.trust_tier}] {entry.id} {entry.title}\n"
            summary = entry.metadata.description or entry.body.strip().split("\n", 1)[0]
            # Pick a matching body sentence after the summary, never dump the whole entry.
            sentences = re.split(r"(?<=[.!?])\s+|\n", entry.body)
            query_terms = set(re.findall(r"\w+", query.casefold()))
            matched = max(
                sentences,
                key=lambda text: len(query_terms & set(re.findall(r"\w+", text.casefold()))),
                default="",
            )
            text = summary + ("\n" + matched if matched and matched != summary else "")
            allowance = remaining - token_cost(header)
            if allowance <= 0:
                continue
            text = sentence_excerpt(text, allowance)
            if not text:
                continue
            cost = token_cost(header + text)
            snippets.append(
                Snippet(
                    id=entry.id,
                    title=entry.title,
                    trust_tier=entry.metadata.trust_tier,
                    stale=entry.metadata.stale,
                    status=entry.metadata.status,
                    scope="private" if entry.private else "project",
                    bootstrap_unreviewed=(
                        entry.metadata.model_extra.get("x-spriteforge-review") == "unreviewed"
                    ),
                    score=score,
                    text=text,
                    tokens=cost,
                )
            )
            remaining -= cost
            if remaining <= 0:
                break
        self._log(
            "snippets", query, scopes, [s.model_dump(exclude={"title", "text"}) for s in snippets]
        )
        return snippets

    def show(self, ident: str, scopes: list[str] | None = None) -> Entry:
        scopes = ["project"] if scopes is None else scopes
        entry = parse(self.path(ident).read_text(encoding="utf-8"), ident)
        if not self.permitted(entry, scopes):
            raise PermissionError("Private memory requires explicit private scope")
        self._log(
            "full-entry", ident, scopes, [{"id": entry.id, "trust_tier": entry.metadata.trust_tier}]
        )
        return entry

    def index(self, directory: str = "", scopes: list[str] | None = None) -> str:
        scopes = ["project"] if scopes is None else scopes
        if directory:
            check_id(directory + "/_entry")
        entries, _ = self.scan()
        visible = [e for e in entries if self.permitted(e, scopes)]
        dirs = CATEGORIES - ({"user-prefs"} if "private" not in scopes else set())
        text = indexes(visible, dirs).get((directory + "/" if directory else "") + "index.md", "")
        self._log("index", directory, scopes, [])
        return text

    def write_batch(
        self,
        entries: list[Entry],
        message: str,
        *,
        actor: str = "spriteforge/0.1",
        verified_by: str | None = None,
        preserve_generation: bool = False,
    ) -> None:
        with git.write_lock(self.root.parent / ".memory.lock"), git.rollback_git(self.root):
            self._write_batch(
                entries,
                message,
                actor=actor,
                verified_by=verified_by,
                preserve_generation=preserve_generation,
            )

    def _write_batch(
        self,
        entries: list[Entry],
        message: str,
        *,
        actor: str,
        verified_by: str | None,
        preserve_generation: bool,
    ) -> None:
        ensure_no_secrets(message)
        if verified_by is not None and not verified_by.startswith("human:"):
            raise ValueError("Human promotion requires a human: verifier")
        existing, errors = self.scan()
        if errors:
            raise ValueError("Corpus contains invalid concepts; validate before writing")
        current = {entry.id: entry for entry in existing}
        if len({e.id for e in entries}) != len(entries):
            raise ValueError("Duplicate IDs in batch")
        now = datetime.now(UTC).isoformat()
        planned = {}
        for entry in entries:
            self.path(entry.id)  # Validate every target before touching any file.
            self.retrieve(entry.title, ["project", "private"], 128)  # Search-before-write.
            metadata = (
                current[entry.id].metadata.model_dump(
                    mode="json", by_alias=True, exclude_unset=True
                )
                if entry.id in current
                else {}
            )
            metadata.update(
                entry.metadata.model_dump(mode="json", by_alias=True, exclude_unset=True)
            )
            if not preserve_generation or "generated" not in metadata:
                metadata["generated"] = {"by": actor, "at": now}
            metadata["verified"] = [{"by": verified_by, "at": now}] if verified_by else []
            if verified_by and metadata.get("x-spriteforge-review") == "unreviewed":
                metadata["verified"][0]["x-spriteforge-designation"] = "bootstrap"
            if entry.private:
                metadata["x-spriteforge-scope"] = "private"
            replacement = Entry(
                id=entry.id, metadata=Metadata.model_validate(metadata), body=entry.body
            )
            planned[entry.id + ".md"] = serialize(replacement)
            current[entry.id] = replacement
        planned.update(indexes(list(current.values()), CATEGORIES))
        old_log = (
            (self.root / "log.md").read_text(encoding="utf-8")
            if (self.root / "log.md").exists()
            else ""
        )
        planned["log.md"] = (
            f"# Knowledge update log\n\n## {now[:10]}\n* {message}\n\n"
            + old_log.removeprefix("# Knowledge update log\n\n")
        )
        planned[".gitignore"] = ".env*\n*.sqlite3*\n__pycache__/\n"
        for content in planned.values():
            ensure_no_secrets(content)
        if any(not (self.root / name).resolve().is_relative_to(self.root) for name in planned):
            raise ValueError("Write target escapes corpus")
        backups = {
            name: (self.root / name).read_bytes() if (self.root / name).exists() else None
            for name in planned
        }
        try:
            for name, content in planned.items():
                target = self.root / name
                if not target.resolve().is_relative_to(self.root):
                    raise ValueError("Index path escapes corpus")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            from .validate import validate

            if validate(self).errors:
                raise ValueError("Write batch failed corpus validation")
            git.commit(self.root, list(planned), message)
        except BaseException:
            for name, content in backups.items():
                target = self.root / name
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            raise

    def promote(self, ident: str, human: str) -> None:
        if not human.startswith("human:"):
            raise ValueError("Verifier must use human:<id>")
        entry = self.show(ident, ["project", "private"])
        self.write_batch(
            [entry],
            f"Human verification: {ident}",
            actor=entry.metadata.generated.by if entry.metadata.generated else "spriteforge/0.1",
            verified_by=human,
            preserve_generation=True,
        )

    def review(self, ident: str) -> None:
        entry = self.show(ident, ["project", "private"])
        if entry.metadata.model_extra.get("x-spriteforge-review") != "unreviewed":
            raise ValueError("Entry is not awaiting bootstrap review")
        entry.metadata.model_extra["x-spriteforge-review"] = "reviewed"
        entry.metadata.model_extra["x-spriteforge-reviewed-at"] = datetime.now(UTC).isoformat()
        self.write_batch(
            [entry],
            f"Review bootstrap entry: {ident}",
            verified_by="human:project-owner",
            preserve_generation=True,
        )

    def export_public(self, destination: Path) -> None:
        destination = destination.resolve()
        if destination.is_relative_to(self.root) or self.root.is_relative_to(destination):
            raise ValueError("Export must be separate from the corpus")
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Export destination must be empty")
        entries, errors = self.scan()
        if errors:
            raise ValueError("Validate corpus before export")
        public = [entry for entry in entries if not entry.private]
        files = {entry.id + ".md": serialize(entry) for entry in public}
        files.update(indexes(public, CATEGORIES - {"user-prefs"}))
        for name, content in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
