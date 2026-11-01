"""L2.1: the whole game as an OKF v0.2 bundle (and the story only when asked for)."""

import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from typer.testing import CliRunner

from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.bundle import Build, Decision, GameRecord, art_info, bundle
from spriteforge.game.cli import app as game_cli
from spriteforge.game.codegen import CodeFile, GameCode, mock_code
from spriteforge.game.codeinfo import read_code
from spriteforge.game.design import fallback_design
from spriteforge.game.gamedb import approx_tokens, context_pack, open_db, promote_all, sync_record
from spriteforge.game.qa import QASuite, fallback_qa
from spriteforge.game.story import fallback_story, fit_story
from spriteforge.memory.okf.codec import UnsafeKnowledge
from spriteforge.memory.okf.validate import links, validate

ROOT = Path(__file__).parents[1]


def report(version: int, **extra) -> dict:
    return {"version": version, "design_version": 1, "passed": True, "chosen_by": "mock",
            "checks": [{"event": "win", "ok": True, "count": 1, "min_count": 1}],
            "attempts": [{"attempt": 1, "passed": True, "stage": "playtest"}],
            "model_calls": {"chat": 5, "image": 4, "estimated_cost_usd": 0.05},
            "art": [], "play": f"/api/runs/x/play/v{version}/index.html", **extra}  # fmt: skip


def record(story: bool = False) -> GameRecord:
    design = fallback_design("arena")
    art = make_art(design, keyword_plan(design), generate=None)
    qa = fallback_qa(design)
    results = {"passed": len(qa.tests), "failed": 0,
               "tests": [{"name": t.name, "passed": True, "failed_step": 0, "failure": ""}
                         for t in qa.tests]}  # fmt: skip
    rec = GameRecord(
        design=design, design_source="game/design/v1.json",
        builds=[Build(1, "game/v1/report.json", report(1, qa=results))],
        art=art_info(art.filled, 1), screen=art.screen, scale=art.scale, credits=art.credits,
        code=mock_code(), qa=QASuite(tests=qa.tests, versions=[1] * len(qa.tests), dropped=[],
                                     notes=[]),
        qa_export=b"# exported QA tests\n", play="/api/runs/x/play/v1/index.html",
        decisions=[Decision("G1 brief", "approved", "survive the arena", "story: No story"),
                   Decision("G2 design v1", "approved", "", "build v1", 1, True)],
    )  # fmt: skip
    if story:
        rec.story, _ = fit_story(fallback_story(design), design)
        rec.story_source = "game/story/v1-design-v1.json"
    return rec


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t"})
    return open_db(tmp_path / "db", store, run.id)


def concepts(db) -> dict:
    return {e.id: e for e in db.scan()[0]}


def conformance(db) -> list[str]:
    """OKF v0.2 as L2.1 asks it, checked from the files (independently of our validator)."""
    problems = []
    root = db.root
    for path in root.rglob("*.md"):
        rel = path.relative_to(root)
        if rel.parts[0].startswith("."):
            continue
        text = path.read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
        meta = yaml.safe_load(front[1]) if front else {}
        if path.name == "index.md" and rel != Path("index.md") and front:
            problems.append(f"{rel}: frontmatter on a non-root index")
        if rel == Path("index.md") and meta != {"okf_version": "0.2"}:
            problems.append("root index must declare okf_version 0.2 only")
        if path.name not in {"index.md", "log.md"}:
            if not str(meta.get("type", "")).strip():
                problems.append(f"{rel}: no type")
            if "okf_version" in meta:
                problems.append(f"{rel}: okf_version outside the root index")
            res = meta.get("resource")
            if res and not (res.startswith("/") and (root / res.lstrip("/")).is_file()):
                problems.append(f"{rel}: resource {res} missing")
        for target in links(text):
            if re.match(r"^[a-z]+:", target):
                continue
            if not target.startswith("/"):
                problems.append(f"{rel}: relative link {target}")
            elif not (root / target.lstrip("/")).is_file():
                problems.append(f"{rel}: broken link {target}")
    log = (root / "log.md").read_text(encoding="utf-8")
    dates = re.findall(r"^## (.+)$", log, re.MULTILINE)
    if not dates or any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates):
        problems.append("log.md dates must be ISO")
    if dates != sorted(dates, reverse=True):
        problems.append("log.md must be newest first")
    return problems


def test_code_modules_of_the_mock_game():
    (module,) = read_code(mock_code().as_dict())
    assert {"enemy_spawn", "enemy_defeat", "gem_collect", "win", "lose"} <= set(module.events)
    assert {"hp", "x", "y", "time", "score"} <= set(module.state)
    assert module.scenes == ["arena"] and module.starts and module.reads_input
    assert {"Arena.update()", "autoplay()"} <= set(module.functions)
    assert module.keys == ["ENTER"] and module.flags == ["god"]
    assert set(module.assets) == {"floor", "hero", "slime", "gem"}
    helper = read_code({"game.js": "sf.start({});", "ui.js": (
        "class Menu extends Phaser.Scene { constructor() { super({key: 'menu'}); }\n"
        "  open() { sf.choose(this, 'LEVEL UP', ['a'], i => {}); sf.state.level = 2; }\n}\n"
        "const k = this.input.keyboard.addKeys('W,A'); sf.event(won ? 'win' : 'lose');")})  # fmt: skip
    assert [m.path for m in helper] == ["ui.js", "game.js"]
    assert helper[0].scenes == ["menu"] and helper[0].menus == ["LEVEL UP"]
    assert helper[0].state == ["level"] and helper[0].keys == ["W", "A"]
    assert helper[0].events == ["win", "lose"] and helper[0].functions == ["Menu.constructor()",
                                                                           "Menu.open()"]  # fmt: skip


def test_bundle_without_a_story_conforms(db):
    rec = record()
    first = sync_record(db, rec, "build v1")
    assert first["files"] == 2 * len(rec.art) + 2  # portrait + sheet each, game.js, the QA export
    ids = set(concepts(db))
    assert {"game", "design/rules", "design/events", "entities/hero", "entities/slime",
            "entities/gem", "entities/floor", "art/style", "art/assets/hero", "code/overview",
            "code/modules/game", "qa/suite", "builds/v1", "decisions/1", "decisions/2"} <= ids  # fmt: skip
    # No story: no story, characters or world concepts (not even their indexes), no invented lore.
    assert not any(i.split("/")[0] in {"story", "characters", "world"} for i in ids)
    assert not (db.root / "story").exists() and not (db.root / "characters").exists()
    hero = concepts(db)["entities/hero"]
    assert "# Character" not in hero.body and "Backstory" not in hero.body
    assert "Story: none (the human did not ask for one)" in concepts(db)["game"].body
    assert validate(db, external=False).errors == []
    assert conformance(db) == []
    # Unchanged record: nothing written; a second write keeps log.md ISO and newest first.
    assert sync_record(db, rec, "again")["written"] == 0
    rec.builds.append(Build(2, "game/v2/report.json", report(2)))
    sync_record(db, rec, "build v2")
    assert conformance(db) == []


def test_code_qa_build_and_decision_concepts(db):
    rec = record()
    sync_record(db, rec, "build v1")
    c = concepts(db)
    module = c["code/modules/game"]
    assert module.metadata.resource == "/references/code/game.js"
    assert (db.root / "references/code/game.js").read_text(encoding="utf-8") == (
        mock_code().files[0].content)  # fmt: skip
    for text in ("`enemy_spawn` (check `spawns`)", "`enemy_defeat`", "`gem_collect`", "`win`",
                 "`lose`", "`hp`, `x`, `y`, `time`, `score`"):  # fmt: skip
        assert text in module.body, text
    events = c["design/events"].body
    assert "# Schema" in events and "| `enemy_spawn` | design check |" in events
    assert "| `hp` | player health | required | `game.js` |" in events
    suite = c["qa/suite"]
    assert suite.metadata.resource == "/references/tests/test_game_qa.py"
    assert "# Schema" in suite.body and "| `do` |" in suite.body
    tests = [i for i in c if i.startswith("qa/tests/")]
    assert len(tests) == len(rec.qa.tests) and "qa/tests/loses-when-idle" in tests
    assert "Passed in build v1." in c["qa/tests/loses-when-idle"].body
    assert "[Decision 2](/decisions/2.md) asked for it." in c["builds/v1"].body
    assert "> survive the arena" in c["decisions/1"].body
    assert "[Build v1](/builds/v1.md) answered it." in c["decisions/2"].body
    asset = c["art/assets/hero"]
    assert asset.metadata.resource == "/references/art/hero.png"
    assert (db.root / "references/art/hero-sheet.png").read_bytes() == rec.art["hero"].sheet

    # A dropped QA test is deprecated, never deleted; a new build and decision appear.
    dropped = rec.qa.tests[1].name
    keep = [t for t in rec.qa.tests if t.name != dropped]
    rec.qa = QASuite(tests=keep, versions=[1] * len(keep), dropped=[dropped], notes=[])
    rec.decisions.append(Decision("G4 playtest v1", "changes requested", f"ignore the {dropped} test",
                                  f"QA tests dropped: {dropped}", 2, True))  # fmt: skip
    rec.builds.append(Build(2, "game/v2/report.json", report(2, revision={
        "design_changes": "", "art_changes": [], "code_instructions": "",
        "drop_qa_tests": [dropped], "story_changes": ""})))  # fmt: skip
    sync_record(db, rec, "build v2")
    c = concepts(db)
    slug = re.sub(r"[^a-z0-9]+", "-", dropped.lower()).strip("-")
    assert c[f"qa/tests/{slug}"].metadata.status == "deprecated"
    assert "QA tests dropped: " + dropped in c["builds/v2"].body
    assert "[Decision 3](/decisions/3.md) asked for it." in c["builds/v2"].body
    assert conformance(db) == []


def test_story_concepts_only_with_a_story(db):
    sync_record(db, record(story=True), "with a story")
    c = concepts(db)
    assert {"story/overview", "story/arc", "story/canon", "world/arena", "characters/hero",
            "characters/slime"} <= set(c)  # fmt: skip
    assert "# Character\n[Hero](/characters/hero.md)" in c["entities/hero"].body
    assert "![Hero](/references/art/hero.png)" in c["characters/hero"].body
    assert conformance(db) == []
    pack = context_pack(db, "level 2", budget=2000)
    assert "(`story/canon`)" in pack and "(`story/overview`)" in pack
    assert "(`entities/hero`)" in pack


def test_trust_tiers_and_secrets(db):
    rec = record()
    sync_record(db, rec, "build v1")
    tiers = lambda: {e.id: e.metadata.trust_tier for e in db.scan()[0]
                     if e.metadata.status == "stable"}  # fmt: skip
    assert set(tiers().values()) == {"unverified"}
    assert promote_all(db, "G4 approved") == len(tiers())
    assert set(tiers().values()) == {"human-reviewed"}
    rec.builds.append(Build(2, "game/v2/report.json", report(2)))
    sync_record(db, rec, "build v2")
    after = tiers()
    assert after["builds/v2"] == after["game"] == "unverified"  # changed or new: unverified again
    assert after["design/rules"] == after["entities/hero"] == "human-reviewed"

    # Code that looks like a credential is withheld (the concept says so); a story is refused.
    rec.code = GameCode(files=[CodeFile(path="game.js", content="const api_key = 'sk-123456789abcdef';\n"
                                        "sf.start({});")], notes="")  # fmt: skip
    sync_record(db, rec, "leaky code")
    module = concepts(db)["code/modules/game"]
    assert module.metadata.resource is None and "withheld" in module.body
    assert "sk-1234" not in (db.root / "references/code/game.js").read_text(encoding="utf-8")
    leaky = record(story=True)
    leaky.story = leaky.story.model_copy(update={"premise": "password = hunter2hunter2"})
    with pytest.raises(UnsafeKnowledge):
        sync_record(db, leaky, "leaky story")
    assert not (db.root / "story").exists()


def test_context_pack_reads_the_record_within_its_budget(db, tmp_path):
    sync_record(db, record(), "build v1")
    pack = context_pack(db, "level 2: a harder arena with more slimes", budget=2000)
    for part in ("(`game`)", "(`design/rules`)", "(`entities/hero`)", "(`art/style`)"):
        assert part in pack, part
    assert "Image: /references/art/hero.png" in pack
    assert "story/" not in pack and approx_tokens(pack) <= 2000
    assert "(`entities/slime`)" in pack  # the focus names slimes
    assert "(`builds/v1`)" not in pack  # history only when the focus asks for it
    assert approx_tokens(context_pack(db, "slime", budget=500)) <= 550
    out = CliRunner().invoke(game_cli, ["db", "context", str(tmp_path), "--focus", "slime"])
    assert out.exit_code == 0 and "(`entities/slime`)" in out.output


def test_game_db_mcp_tools_over_stdio(db, tmp_path):
    sync_record(db, record(), "build v1")

    async def exercise():
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "spriteforge", "game", "db", "mcp", str(tmp_path)],
            env=get_default_environment(),
            cwd=str(ROOT),
        )
        async with stdio_client(server) as (read, write), ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=60)
        ) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {"game_index", "game_search", "game_show", "game_context"}
            assert all(t.annotations.readOnlyHint for t in tools.values())
            call = lambda name, **args: session.call_tool(name, args)
            index = (await call("game_index")).content[0].text
            assert "okf_version" in index and "[entities](/entities/index.md)" in index
            section = (await call("game_index", dir="code/modules")).content[0].text
            assert "[game.js](/code/modules/game.md)" in section
            hits = (await call("game_search", query="gem collect")).structuredContent["result"]
            assert hits and {"id", "title", "trust_tier", "text"} <= set(hits[0])
            shown = (await call("game_show", id="code/modules/game")).content[0].text
            assert shown.startswith("---\ntype: Code Module") and "/references/code/game.js" in shown
            pack = (await call("game_context", focus="level 2", budget=800)).content[0].text
            assert "(`game`)" in pack and approx_tokens(pack) <= 850
            missing = await call("game_show", id="../state")
            assert missing.isError

    anyio.run(exercise)


def test_make_folder_record_round_trip(tmp_path):
    """`game db sync` on a `game make` folder writes the same bundle as the build did."""
    from spriteforge.config import load_config
    from spriteforge.game.pipeline import make_game

    cfg = load_config()
    cfg.game.review = False
    out = tmp_path / "g"
    result = make_game("arena", out, cfg, mock=True, record=False, log=lambda m: None)
    assert not (out / "story.json").exists() and result.report["story"] is False
    store = Store(tmp_path / "s.sqlite3")
    db = open_db(out / "db", store, store.create_run({}).id)
    before = {e.id: e.body for e in db.scan()[0]}
    assert {"game", "code/modules/game", "qa/suite", "builds/v1", "decisions/1"} <= set(before)
    assert "> arena" in before["decisions/1"] and conformance(db) == []
    assert json.loads((out / "report.json").read_text())["game_db"]["concepts"] == len(before)
    out_cli = CliRunner().invoke(game_cli, ["db", "sync", str(out)])
    assert out_cli.exit_code == 0 and out_cli.output.startswith("0 concept(s)"), out_cli.output
    assert {e.id: e.body for e in db.scan()[0]} == before
    entries, _ = bundle(record())
    assert all(e.metadata.type for e in entries)
