from datetime import UTC, datetime, timedelta
from subprocess import CompletedProcess

import pytest
import yaml
from typer.testing import CliRunner

from spriteforge.cli import app
from spriteforge.memory.okf import git
from spriteforge.memory.okf.codec import UnsafeKnowledge, parse, serialize
from spriteforge.memory.okf.models import Entry, Metadata
from spriteforge.memory.okf.seed import seed, seed_entries
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.memory.okf.validate import validate


@pytest.fixture
def memory(tmp_path):
    return KnowledgeStore(tmp_path / "memory")


def entry(ident="lessons/test", title="Test lesson", body="Test useful knowledge.", **fields):
    return Entry(id=ident, metadata=Metadata(type="Lesson", title=title, **fields), body=body)


def test_minimal_okf_and_unknown_fields_roundtrip():
    text = "---\ntype: Unknown Producer Type\nx-other: {nested: [one, two]}\n---\nA concept.\n"
    concept = parse(text, "genres/custom")
    assert concept.metadata.trust_tier == "unverified"
    assert concept.metadata.status == "stable"
    assert parse(serialize(concept), concept.id) == concept
    data = yaml.safe_load(serialize(concept).split("---")[1])
    assert data == {"type": "Unknown Producer Type", "x-other": {"nested": ["one", "two"]}}


@pytest.mark.parametrize(
    "text",
    ["No frontmatter", "---\n[]\n---\n", "---\ntitle: Missing type\n---\n", "---\ntype: ''\n---\n"],
)
def test_malformed_frontmatter_rejected(text):
    with pytest.raises(ValueError):
        parse(text, "lessons/invalid")


@pytest.mark.parametrize(
    "verified,tier",
    [
        (None, "unverified"),
        ({"by": "process:tests", "at": "2026-10-28T00:00:00Z"}, "machine-confirmed"),
        ([{"by": "human:owner", "at": "2026-10-28T00:00:00Z"}], "human-reviewed"),
    ],
)
def test_trust_derived_only_from_standard_verification(verified, tier):
    data = {"type": "Lesson", "trust_tier": "fake-human-review", "generated": {"by": "agent/1"}}
    if verified is not None:
        data["verified"] = verified
    concept = parse("---\n" + yaml.safe_dump(data) + "---\nBody", "lessons/trust")
    assert concept.metadata.trust_tier == tier
    assert parse(serialize(concept), concept.id).metadata.trust_tier == tier


def test_provenance_and_timezone_validation():
    concept = entry(
        sources=[
            {"resource": "https://example.com/source", "id": "policy", "author": "human:owner"}
        ],
        stale_after=datetime.now(UTC) - timedelta(days=1),
    )
    assert concept.metadata.stale
    assert parse(serialize(concept), concept.id).metadata.sources[0].id == "policy"
    with pytest.raises(ValueError):
        entry(sources=[{"title": "No resource"}])
    with pytest.raises(ValueError):
        entry(stale_after="2026-10-28T00:00:00")


def test_agent_writes_human_promotion_and_reverification(memory):
    memory.write_batch([entry(**{"x-other": "keep"})], "Record agent lesson")
    initial = memory.show("lessons/test")
    assert initial.metadata.trust_tier == "unverified"
    assert initial.metadata.generated.by == "spriteforge/0.1"
    memory.promote("lessons/test", "human:owner")
    promoted = memory.show("lessons/test")
    assert promoted.metadata.trust_tier == "human-reviewed"
    assert promoted.metadata.generated == initial.metadata.generated
    memory.write_batch([entry(body="Changed content")], "Revise lesson after new evidence")
    updated = memory.show("lessons/test")
    assert updated.metadata.trust_tier == "unverified"
    assert updated.metadata.model_extra["x-other"] == "keep"
    assert len(git.git(memory.root, "log", "--format=%s").splitlines()) == 3
    assert not validate(memory, external=False).errors


def test_private_preferences_never_default_search_or_export(memory, tmp_path):
    memory.write_batch(
        [
            entry("user-prefs/controls", "Confidential preference", "Prefer left-hand controls."),
            entry("genres/local", "Local genre", "Local knowledge."),
        ],
        "Record scoped entries",
    )
    assert not memory.retrieve("Confidential")
    assert memory.retrieve("Confidential", scopes=["private"])[0].scope == "private"
    with pytest.raises(PermissionError):
        memory.show("user-prefs/controls")
    assert "Confidential" not in memory.index()
    assert "Confidential" not in memory.index("user-prefs")
    assert "Confidential" in memory.index("user-prefs", ["private"])
    export = tmp_path / "export"
    memory.export_public(export)
    assert not (export / "user-prefs").exists()
    assert not (export / ".git").exists()
    assert "Confidential" not in "\n".join(p.read_text() for p in export.rglob("*.md"))


def test_private_extension_on_other_directory_is_also_protected(memory, tmp_path):
    memory.write_batch(
        [entry("lessons/private", "Hidden lesson", **{"x-spriteforge-scope": "private"})],
        "Private lesson",
    )
    assert not memory.retrieve("Hidden")
    assert "Hidden" not in memory.index("lessons")
    memory.export_public(tmp_path / "public")
    assert not (tmp_path / "public/lessons/private.md").exists()


def test_search_bm25_budget_staleness_and_retrieval_events(memory):
    memory.write_batch(
        [
            entry(
                "genres/metroidvania",
                "Metroidvania map",
                "Map ability gates and backtracking.",
                stale_after=datetime.now(UTC) - timedelta(days=1),
            ),
            entry("lessons/other", "Other lesson", "Neutral material."),
        ],
        "Add searchable knowledge",
    )
    before = len(memory.events.events(memory.run_id))
    result = memory.retrieve("metroidvania map", token_budget=180)
    assert result[0].id == "genres/metroidvania" and result[0].stale
    assert sum(s.tokens for s in result) <= 180
    assert not memory.retrieve("metroidvania", token_budget=0)
    assert len(memory.events.events(memory.run_id)) == before + 2
    event = memory.events.events(memory.run_id)[-2]
    assert event.type == "memory.retrieved" and event.agent == "Librarian"
    assert event.payload["results"][0]["id"] == "genres/metroidvania"
    assert "text" not in event.payload["results"][0]


def test_drop_in_genre_appears_without_restarting(memory):
    assert not memory.retrieve("clockwork skating")
    path = memory.root / "genres/clockwork-skating.md"
    path.parent.mkdir()
    path.write_text(
        "---\ntype: Custom Genre\ntitle: Clockwork skating\ntags: [skating]\n---\nPrecision skating arenas."
    )
    assert memory.retrieve("clockwork skating")[0].id == "genres/clockwork-skating"
    assert "Clockwork skating" in memory.index("genres")
    assert not validate(memory, external=False).errors


@pytest.mark.parametrize(
    "ident",
    ["../escape", "/absolute", "lessons/../outside", "index", ".git/evil", "lessons/log", "x\\y"],
)
def test_safe_ids(memory, ident):
    with pytest.raises(ValueError):
        memory.write_batch([entry(ident)], "Invalid write")
    assert not list(memory.root.rglob("test.md"))


def test_secret_rejection_precedes_writes_and_events(memory, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "FAKE-TEST-SECRET")
    for body in ["FAKE-TEST-SECRET", "api_key=unknown-fake-secret", "Bearer credential-value"]:
        with pytest.raises(UnsafeKnowledge):
            memory.write_batch([entry(body=body)], "Attempt unsafe write")
    assert not (memory.root / "lessons/test.md").exists()
    with memory.events.connect() as db:
        assert "FAKE-TEST-SECRET" not in "\n".join(db.iterdump())


def test_failed_batch_restores_existing_files(memory, monkeypatch):
    memory.write_batch([entry()], "Initial entry")
    before = (memory.root / "lessons/test.md").read_bytes()

    def fail(*args):
        raise RuntimeError("Simulated Git failure")

    monkeypatch.setattr(git, "commit", fail)
    with pytest.raises(RuntimeError):
        memory.write_batch([entry(body="Changed")], "Should rollback")
    assert (memory.root / "lessons/test.md").read_bytes() == before


def test_validator_errors_are_structured_and_indexes_optional(memory):
    (memory.root / "valid.md").write_text(
        "---\ntype: Any Unknown Type\n---\n[Missing](/missing.md)"
    )
    assert not validate(memory, external=False).errors
    (memory.root / "invalid.md").write_text("---\ntype: [\n---\n")
    assert validate(memory, external=False).errors
    (memory.root / "invalid.md").unlink()
    (memory.root / "log.md").write_text("# Updates\n## 2026-99-99\n* Bad date\n")
    assert validate(memory, external=False).errors


def test_optional_external_validator_is_invoked(memory, monkeypatch):
    import importlib

    module = importlib.import_module("spriteforge.memory.okf.validate")
    monkeypatch.setattr(module.shutil, "which", lambda name: "okf-binary")
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return CompletedProcess(args, 0, "External PASS", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    report = validate(memory)
    assert seen == [["okf-binary", "validate", str(memory.root)]]
    assert report.external == "External PASS"


def test_seed_library_complete_idempotent_and_git_committed(memory):
    assert seed(memory) == 19
    assert seed(memory) == 0
    assert all(e.metadata.trust_tier == "human-reviewed" for e in memory.scan()[0])
    assert "Seed 10 genres" in git.git(memory.root, "log", "-1", "--format=%s")
    assert not validate(memory, external=False).errors
    for genre in [e for e in seed_entries() if e.id.startswith("genres/")]:
        for heading in [
            "Perspective",
            "Controls",
            "Core mechanics",
            "Typical entities",
            "Recommended sizes",
            "Palette suggestions",
            "Asset checklist",
            "Animation needs",
            "Common pitfalls",
            "Adaptive questions",
        ]:
            assert heading in genre.body
        assert "48x48" in genre.body and "16x16" in genre.body


def test_memory_cli_acceptance(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app, ["memory", "search", "metroidvania map", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "human-reviewed" in result.output and "genres/metroidvania" in result.output
    result = runner.invoke(app, ["memory", "validate", "--workspace", str(tmp_path)])
    assert result.exit_code == 0 and "PASS" in result.output


def test_writer_serializes_concurrent_batches(memory):
    from concurrent.futures import ThreadPoolExecutor

    def write(i):
        memory.write_batch([entry(f"lessons/parallel-{i}", f"Parallel {i}")], f"Parallel batch {i}")

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(write, range(2)))
    assert len(memory.scan()[0]) == 2
    assert len(git.git(memory.root, "log", "--format=%s").splitlines()) == 2
    assert not validate(memory, external=False).errors


def test_unknown_scope_key_is_not_reinterpreted_as_extension():
    text = "---\ntype: Custom\nscope: external-project-field\n---\nBody"
    concept = parse(text, "lessons/custom-scope")
    assert concept.metadata.scope == "project"
    assert "scope: external-project-field" in serialize(concept)


def test_invalid_index_and_cli_validation_on_unseeded_invalid_corpus(memory):
    (memory.root / "index.md").write_text("Not an index section")
    assert validate(memory, external=False).errors
    (memory.root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Knowledge\n')
    assert not validate(memory, external=False).errors
    (memory.root / "broken.md").write_text("No frontmatter")
    result = CliRunner().invoke(app, ["memory", "validate", "--workspace", str(memory.root.parent)])
    assert result.exit_code == 1 and "broken.md" in result.output and "FAIL" in result.output
