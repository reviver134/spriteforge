"""M11.1: the story bible and the game database (OKF canon later designs read)."""

import io

import pytest
from PIL import Image
from typer.testing import CliRunner

from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.cli import app as game_cli
from spriteforge.game.design import fallback_design
from spriteforge.game.gamedb import (
    approx_tokens,
    context_pack,
    open_db,
    promote_all,
    sync_build,
    sync_story,
)
from spriteforge.game.story import Character, fallback_story, fit_story
from spriteforge.memory.okf.codec import UnsafeKnowledge
from spriteforge.memory.okf.validate import validate

SOURCES = {"story": "story.json", "design": "design.json", "build": "report.json"}


def char(entity, name, role="enemy") -> Character:
    return Character(entity=entity, name=name, role=role, look="a look", personality="calm",
                     backstory="old", goal="win", abilities="moves", relationships="")  # fmt: skip


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t"})
    return open_db(tmp_path / "db", store, run.id)


def trust(db) -> dict[str, str]:
    return {e.id: e.metadata.trust_tier for e in db.scan()[0] if e.metadata.status == "stable"}


def test_story_is_fitted_to_the_design():
    design = fallback_design("arena")
    story = fallback_story(design).model_copy(update={
        "characters": [char("Slime", "Goo"), char("slime", "Second goo"), char("dragon", "Fafnir"),
                       char("gem", "Goo")],
        "locations": [fallback_story(design).locations[0]] * 2,
        "arc": [f"beat {n}" for n in range(12)],
    })  # fmt: skip
    fitted, notes = fit_story(story, design)
    assert [(c.entity, c.name) for c in fitted.characters] == [
        ("hero", "Hero"), ("slime", "Goo"), ("gem", "Goo 2")]  # fmt: skip
    assert len(fitted.locations) == 1 and len(fitted.arc) == 7
    assert any("dragon" in n for n in notes) and any("already has a character" in n for n in notes)
    assert any("player entity 'hero'" in n for n in notes)


def test_database_entries_trust_and_images(db):
    design = fallback_design("arena")
    story, _ = fit_story(fallback_story(design), design)
    sync_story(db, story, design, "story.json", "design.json")
    ids = set(trust(db))
    assert {"story/overview", "story/arc", "story/canon", "world/arena", "gameplay/rules",
            "characters/hero", "characters/slime"} <= ids  # fmt: skip
    assert set(trust(db).values()) == {"unverified"}
    assert promote_all(db, "G2 approved") == len(ids)
    assert set(trust(db).values()) == {"human-reviewed"}

    art = make_art(design, keyword_plan(design), generate=None)
    first = sync_build(db, story, design, art, 1, {"passed": True}, SOURCES)
    assert first["files"] == 4  # hero and slime: a 4x portrait and the sheet each
    hero = next(e for e in db.scan()[0] if e.id == "characters/hero")
    assert hero.metadata.resource == "characters/hero.png" and "![Hero](hero.png)" in hero.body
    assert Image.open(db.root / "characters" / "hero.png").size[0] >= 64  # 4x the 16 px frame
    tiers = trust(db)
    assert tiers["story/overview"] == "human-reviewed"  # unchanged by the build
    assert (
        tiers["characters/hero"] == tiers["art/assets"] == tiers["levels/level-1"] == "unverified"
    )
    assert sync_build(db, story, design, art, 1, {"passed": True}, SOURCES)["written"] == 0

    # Redrawn art changes its character entry; a renamed character leaves the old one deprecated.
    art.filled["slime"].png = png(Image.new("RGBA", (16, 16), (200, 30, 30, 255)))
    promote_all(db, "G4 approved")
    renamed = story.model_copy(update={"characters": [story.characters[0]]})
    sync_build(db, renamed, design, art, 2, {"passed": True}, SOURCES)
    status = {e.id: e.metadata.status for e in db.scan()[0]}
    assert status["characters/slime"] == "deprecated"
    assert validate(db, external=False).errors == []

    # Secrets never reach the database.
    leaky = story.model_copy(update={"premise": "api_key = sk-123456789abcdef"})
    with pytest.raises(UnsafeKnowledge):
        sync_story(db, leaky, design, "story.json", "design.json")
    assert "sk-123456789" not in (db.root / "story" / "overview.md").read_text(encoding="utf-8")


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_context_pack_always_has_the_canon_and_follows_the_focus(db, tmp_path):
    design = fallback_design("arena")
    story, _ = fit_story(fallback_story(design), design)
    story = story.model_copy(update={"characters": [*story.characters,
                                                    char("gem", "Shard Keeper", "ally")]})  # fmt: skip
    art = make_art(design, keyword_plan(design), generate=None)
    sync_build(db, story, design, art, 1, {"passed": True}, SOURCES)
    plain = context_pack(db, budget=2000)
    for part in ("(`story/overview`)", "(`story/canon`)", "(`story/arc`)", "(`characters/hero`)",
                 "(`gameplay/rules`)", "(`art/style`)"):  # fmt: skip
        assert part in plain, part
    assert f"Image: {db.root / 'characters' / 'hero.png'}" in plain
    assert "(`characters/gem`)" not in plain  # only named in the cast list; not asked for
    focused = context_pack(db, "a level where the shard keeper guards the gems", budget=2000)
    assert "(`characters/gem`)" in focused and "Shard Keeper" in focused
    small = context_pack(db, "shard keeper", budget=600)
    assert approx_tokens(small) <= 650
    out = CliRunner().invoke(game_cli, ["db", str(tmp_path), "--focus", "shard keeper"])
    assert out.exit_code == 0 and "(`characters/gem`)" in out.output
