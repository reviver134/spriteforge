"""M10.3: the Art Reviewer judges the whole art set in the running game and fixes blockers."""

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import ArtResult, Filled, keyword_plan, make_art, resolve_plan
from spriteforge.game.art_checks import precheck
from spriteforge.game.art_review import (
    ArtReview,
    Named,
    ReviewIssue,
    Scores,
    entity_issues,
    fallback_review,
    review_sheets,
    review_slots,
)
from spriteforge.game.build import build_game
from spriteforge.game.codegen import mock_code
from spriteforge.game.design import Entity, fallback_design
from spriteforge.game.pipeline import BuildOutcome, Models, review_art
from spriteforge.game.playtest import playtest, review_times
from spriteforge.library import add_pack
from spriteforge.providers.base import ChatResult
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider

# Sheet order of the fallback design's art: player, enemy, pickup, tile.
HERO, SLIME, GEM, FLOOR = 1, 2, 3, 4


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ent(eid, role, look, w=16, h=16) -> Entity:
    return Entity(id=eid, role=role, look=look, behaviour="-", width=w, height=h, animations=[])


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


class Images(MockImageProvider):
    """Mock Muse Image that records every request and whether it was an edit."""

    def __init__(self, budget):
        super().__init__(budget)
        self.calls = []

    def generate(self, request):
        self.calls.append((request, bool(request.references)))
        return super().generate(request)

    def edit(self, request):
        return self.generate(request)


class Reviewer(MockLLMProvider):
    """Answers every review with `answer` and records the requests."""

    def __init__(self, budget, answer: ArtReview):
        super().__init__(budget, [])
        self.answer, self.requests = answer, []

    def chat(self, request):
        with self.budget.attempt("mock", "scripted-llm"):
            self.requests.append(request)
            return ChatResult(content=self.answer.model_dump_json())


def answer(*issues, named=()) -> ArtReview:
    scores = Scores(consistency=4, readability=3, perspective=5, scale=4, palette=4)
    return ArtReview(
        scores=scores,
        named=[Named(slot=s, seen_as=t) for s, t in named],
        issues=[
            ReviewIssue(slot=s, problem=p, severity=v, fix_prompt=f"fix {p}") for s, p, v in issues
        ],
    )


def shot(color) -> dict:
    return {"t": 1.0, "png": png(Image.new("RGB", (320, 180), color))}


def mock_build(tmp_path, design, art, budget):
    """The fixture game built and played with `art`: what a review starts from."""
    code = mock_code()
    build = build_game(tmp_path / "out" / "build", title="t", controls="-", files=code.as_dict(),
                       assets=art.manifest(), images=art.files(), credits=[])  # fmt: skip
    report = {"stage": "playtest", **playtest(build, design.checks, design.session_seconds)}
    return BuildOutcome(
        code, report, build, [], [shot("#203040"), shot("#304050"), shot("#405060")]
    )


def models(budget, reviewer=None, images=None, max_fixes=4) -> Models:
    cfg = load_config()
    cfg.game.review_max_fixes = max_fixes
    return Models(cfg, budget, True, reviewer, images)


def fallback_art(budget, design=None):
    design = design or fallback_design("arena")
    plan = keyword_plan(design)
    images = Images(budget)
    art = make_art(design, plan, generate=lambda r, e: images.generate(r).data)
    images.calls.clear()
    return design, plan, art, images


# --- pre-checks ----------------------------------------------------------------------------


def sprite(img: Image.Image, role: str, source: str = "muse") -> Filled:
    meta = {"role": role, "frame": list(img.size), "anims": {"idle": [0, 1]}}
    return Filled(png(img), source, [], meta, "sprite")


def square(size, color, outline=None) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((1, 1, size - 2, size - 2), fill=color, outline=outline)
    return img


def test_prechecks_flag_contrast_hue_size_placeholder():
    d = fallback_design("arena")
    d.entities = [
        ent("hero", "player", "a knight"),
        ent("slime", "enemy", "a cyan slime"),
        ent("bat", "enemy", "a black bat"),
        ent("gem", "pickup", "a cyan gem", 8, 8),
        ent("orb", "pickup", "a glowing orb", 8, 8),
        ent("floor", "tile", "dark stone floor"),
    ]
    floor = Image.new("RGBA", (16, 16), (25, 25, 35, 255))
    filled = {
        "hero": sprite(square(16, (240, 220, 90, 255), (20, 20, 20, 255)), "player"),
        "slime": sprite(square(16, (0, 200, 220, 255)), "enemy"),
        "bat": sprite(square(16, (20, 20, 30, 255)), "enemy"),  # dark on a dark floor
        "gem": sprite(square(48, (0, 190, 230, 255)), "pickup"),  # cyan like the slime, 3x hero
        "orb": sprite(square(8, (128, 128, 128, 255)), "pickup", "placeholder"),
        "floor": Filled(png(floor), "muse", [], {"role": "tile", "size": [16, 16]}, "image"),
    }
    art = ArtResult((320, 180), 1, "#000", filled, {}, [], [])
    found = {(f.entity, f.check) for f in precheck(d, art)}
    assert ("bat", "contrast") in found and ("hero", "contrast") not in found
    assert ("gem", "hue") in found
    assert ("gem", "size") in found and ("slime", "size") not in found
    assert ("orb", "placeholder") in found
    by_check = {f.check: f for f in precheck(d, art)}
    assert by_check["placeholder"].severity == "blocking"
    hero_size = next(f.detail for f in precheck(d, art) if f.entity == "gem" and "hero" in f.detail)
    assert "3.3x the hero's size; the design says 0.5x" == hero_size


def test_offline_review_is_built_from_the_prechecks(budget):
    design, _, art, _ = fallback_art(budget)
    gem = art.filled["gem"]
    art.filled["gem"] = Filled(gem.png, "placeholder", [], gem.meta, "sprite")
    review = fallback_review(review_slots(art), precheck(design, art), design)
    assert [(i.slot, i.severity) for i in review.issues] == [(GEM, "blocking")]
    assert review.scores.consistency < 5 and review.named == []


# --- the vision review ---------------------------------------------------------------------


def test_review_request_carries_sheet_and_screenshots(tmp_path, budget):
    design, plan, art, _ = fallback_art(budget)
    reviewer = Reviewer(budget, answer())
    outcome = BuildOutcome(mock_code(), {"passed": True}, tmp_path, [],
                           [shot("#102030"), shot("#203040"), shot("#304050")])  # fmt: skip
    reviewed = review_art(models(budget, reviewer), design, plan, art, outcome, load_config(),
                          tmp_path, lambda m: None)  # fmt: skip
    (request,) = reviewer.requests
    message = request.messages[-1]
    sheet, zoomed = review_sheets(art, review_slots(art))
    assert len(message.images) == 5 and message.images[:2] == [sheet, zoomed]
    assert all(Image.open(io.BytesIO(i)).width == 640 for i in message.images[2:])  # shots 2x
    for e in design.entities:
        assert f"`{e.id}`" in message.content and e.look in message.content
    # The naming test is blind: no entity is given its sheet number in the prompt.
    assert "`hero` (" in message.content and "1: hero" not in message.content
    assert request.reasoning_effort == "max" and request.cache_key == "spriteforge-review"
    assert request.json_schema["title"] == "ArtReview"
    assert reviewed.report["scores"]["readability"] == 3 and reviewed.report["fixes"] == []
    assert set(reviewed.images) == {"sheet.png", "sheet-4x.png", "shot-1.png", "shot-2.png",
                                    "shot-3.png"}  # fmt: skip


def test_failed_review_call_keeps_the_build(tmp_path, budget):
    class Down(MockLLMProvider):
        def chat(self, request):
            raise RuntimeError("Meta HTTP 500: internal server error")

    design, plan, art, _ = fallback_art(budget)
    outcome = BuildOutcome(mock_code(), {"passed": True}, tmp_path, [], [])
    reviewed = review_art(models(budget, Down(budget)), design, plan, art, outcome,
                          load_config(), tmp_path, lambda m: None)  # fmt: skip
    assert reviewed.report["reviewed_by"].startswith(
        "pre-checks only (the vision review failed: RuntimeError: Meta HTTP 500"
    )
    assert reviewed.art is art and reviewed.outcome is outcome


def test_naming_miss_becomes_issue(budget):
    design, _, art, _ = fallback_art(budget)
    art.filled["floor"] = Filled(art.filled["floor"].png, "placeholder", [],
                                 art.filled["floor"].meta, "image")  # fmt: skip
    reply = answer(
        (9, "nothing there", "major"),
        named=[(GEM, "a blue blob"), (SLIME, "a green slime"), (HERO, "small round thing")],
    )
    issues, named, notes = entity_issues(reply, review_slots(art), design, art)
    assert {n["entity"]: n["ok"] for n in named} == {"gem": False, "slime": True, "hero": False}
    misses = [i for i in issues if i.origin == "naming test"]
    assert [(i.entity, i.severity) for i in misses] == [("gem", "major"), ("hero", "major")]
    assert "reads as 'a blue blob'" in misses[0].problem
    # Code also reports a placeholder the reviewer missed, as blocking.
    assert any(i.entity == "floor" and i.severity == "blocking" for i in issues)
    assert notes and "unknown sprite number 9" in notes[0]
    assert [i.n for i in issues] == list(range(1, len(issues) + 1))


# --- fixes ---------------------------------------------------------------------------------


def test_blocking_issue_fixed_once_with_high_reasoning(tmp_path, budget):
    design, plan, art, images = fallback_art(budget)
    before = {eid: f.png for eid, f in art.filled.items()}
    reply = answer((SLIME, "invisible on the floor", "blocking"),
                   (GEM, "reads as a slime", "blocking"),
                   (HERO, "outline a bit thin", "minor"))  # fmt: skip
    outcome = mock_build(tmp_path, design, art, budget)
    reviewed = review_art(models(budget, Reviewer(budget, reply), images), design, plan, art,
                          outcome, load_config(), tmp_path / "out", lambda m: None)  # fmt: skip
    assert len(images.calls) == 2
    for request, edit in images.calls:
        assert edit and request.reasoning_strength == "high"
        assert "Revise the FIRST image" in request.prompt and "Fix: fix " in request.prompt
    raws = {art.filled[e].record["raw"] for e in ("slime", "gem")}
    assert {r.references[0] for r, _ in images.calls} == raws  # edits of the current images
    new = reviewed.art.filled
    assert new["slime"].source == new["gem"].source == "muse (review fix)"
    assert new["hero"].png == before["hero"] and new["floor"].png == before["floor"]
    issues = {i["n"]: i for i in reviewed.report["issues"]}
    assert issues[1]["status"] == issues[2]["status"] == "fixed"
    assert issues[3]["status"] == "suggestion" and issues[3]["severity"] == "minor"
    assert {"slime-before.png", "slime-after.png", "gem-after.png"} <= set(reviewed.images)
    assert reviewed.report["replay"] == {"passed": True, "reverted": False}
    # The reviewed plan asks for the fixed art, so the next version locks it.
    picks = {p.entity: p for p in reviewed.plan.picks}
    assert picks["slime"].reason.startswith("art review:") and picks["hero"] == next(
        p for p in plan.picks if p.entity == "hero"
    )
    record = {**new["slime"].record, "png": new["slime"].png, "version": 1}
    images.calls.clear()
    again = make_art(design, reviewed.plan, generate=lambda r, e: images.generate(r).data,
                     previous=lambda e: record if e == "slime" else None)  # fmt: skip
    assert again.filled["slime"].source == "muse (locked, from v1)"
    assert again.filled["slime"].png == new["slime"].png
    assert not any("fix invisible on the floor" in r.prompt for r, _ in images.calls)


def test_fix_cap_and_no_second_review(tmp_path, budget):
    d = fallback_design("arena")
    d.entities += [ent("bat", "enemy", "a purple bat"), ent("coin", "pickup", "a gold coin", 8, 8)]
    design, plan, art, images = fallback_art(budget, d)
    slots = review_slots(art)
    reply = answer(*[(n, f"problem {n}", "blocking") for n in range(1, len(slots) + 1)])
    reviewer = Reviewer(budget, reply)
    outcome = mock_build(tmp_path, design, art, budget)
    reviewed = review_art(models(budget, reviewer, images, max_fixes=4), design, plan, art,
                          outcome, load_config(), tmp_path / "out", lambda m: None)  # fmt: skip
    assert len(reviewer.requests) == 1 and len(images.calls) == 4
    fixed = [f["entity"] for f in reviewed.report["fixes"] if f["fixed"]]
    assert fixed == ["hero", "slime", "bat", "gem"]  # priority order: player, enemies, pickups
    unfixed = [i for i in reviewed.report["issues"] if i["status"] != "fixed"]
    assert [i["entity"] for i in unfixed] == ["coin", "floor"]
    assert all(i["status"] == "not fixed: fix cap (4) reached" for i in unfixed)


def test_fixes_are_reverted_when_the_game_breaks(tmp_path, budget, monkeypatch):
    from spriteforge.game import pipeline

    design, plan, art, images = fallback_art(budget)
    outcome = mock_build(tmp_path, design, art, budget)
    real = pipeline.playtest

    def broken(*args, **kwargs):  # the replay with the new art fails its checks
        return {**real(*args, **kwargs), "passed": False, "errors": ["boom"]}

    monkeypatch.setattr(pipeline, "playtest", broken)
    reviewed = review_art(models(budget, Reviewer(budget, answer((SLIME, "x", "blocking"))),
                                 images), design, plan, art, outcome, load_config(),
                          tmp_path / "out", lambda m: None)  # fmt: skip
    assert reviewed.report["replay"]["reverted"] and reviewed.art is art
    assert reviewed.outcome.report["passed"] and reviewed.plan == plan
    assert reviewed.report["issues"][0]["status"].startswith("not fixed: reverted")


def test_fixes_are_reverted_when_a_qa_test_breaks(tmp_path, budget, monkeypatch):
    """Art sets hitboxes: a QA test that passed with the old art and fails with the fixed art
    reverts the fixes, like a broken bot playtest does."""
    from dataclasses import replace

    from spriteforge.game import pipeline
    from spriteforge.game.qa import QATest, blank_step

    design, plan, art, images = fallback_art(budget)
    outcome = mock_build(tmp_path, design, art, budget)
    qa = [QATest(name="loses when idle", purpose="p", god=False,
                 steps=[blank_step(do="expect_no_errors")])]  # fmt: skip
    outcome = replace(outcome, qa=[{"name": "loses when idle", "passed": True}])
    calls = []

    def rerun(build, tests, fps=30):
        calls.append([t.name for t in tests])
        return [{"name": "loses when idle", "passed": False, "failure": "no lose",
                 "screenshots": []}]  # fmt: skip

    monkeypatch.setattr(pipeline, "run_qa", rerun)
    reviewed = review_art(models(budget, Reviewer(budget, answer((SLIME, "x", "blocking"))),
                                 images), design, plan, art, outcome, load_config(),
                          tmp_path / "out", lambda m: None, qa=qa)  # fmt: skip
    assert calls == [["loses when idle"]]
    assert reviewed.report["replay"] == {"passed": True, "reverted": True,
                                         "qa": {"passed": 0, "failed": 1}}  # fmt: skip
    assert (
        "reverted: QA tests failed with the new art: 'loses when idle'" in reviewed.report["notes"]
    )
    assert reviewed.art is art and reviewed.outcome.qa == outcome.qa


def strip(path: Path, frames: int, size: int, color) -> None:
    img = Image.new("RGBA", (frames * size, size))
    for n in range(frames):
        ImageDraw.Draw(img).rectangle((n * size + 4, 4, n * size + size - 5, size - 1), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def test_non_ai_allowed_library_art_is_redrawn_not_sent(tmp_path, budget, monkeypatch):
    lib = tmp_path / "library"
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    src = tmp_path / "pack"
    strip(src / "Hero" / "Knight" / "Idle.png", 2, 32, (200, 60, 60, 255))
    strip(src / "Hero" / "Knight" / "Run.png", 4, 32, (200, 60, 60, 255))
    add_pack(src, "noai", category="prop", categorize={"Knight": "character"}, cell="auto",
             name_depth=2, tags=["top-down"], license="CC0", license_verified=True,
             ai_allowed=False, root=lib)  # fmt: skip
    design = fallback_design("arena")
    design.entities[0].look = "a brave knight"
    design, plan, art, images = fallback_art(budget, design)
    assert art.filled["hero"].source == "library noai/knight"
    library_png = art.filled["hero"].png
    outcome = mock_build(tmp_path, design, art, budget)
    reply = answer((HERO, "unreadable in crowds", "blocking"))
    reviewed = review_art(models(budget, Reviewer(budget, reply), images), design, plan, art,
                          outcome, load_config(), tmp_path / "out", lambda m: None)  # fmt: skip
    ((request, edit),) = images.calls
    assert not edit and request.references == []  # a fresh draw from text; nothing sent
    assert library_png not in request.references
    assert reviewed.art.filled["hero"].source == "muse (review fix)"
    (fix,) = reviewed.report["fixes"]
    assert "forbids AI use" in fix["mode"] and fix["fixed"]
    # The fix survives the next version: library-first does not undo an explicit redraw.
    picks, _ = resolve_plan(reviewed.plan, design)
    assert picks["hero"].source == "muse"


def test_playtest_captures_screenshots(tmp_path, budget):
    design, _, art, _ = fallback_art(budget)
    build = build_game(tmp_path / "build", title="t", controls="-", files=mock_code().as_dict(),
                       assets=art.manifest(), images=art.files(), credits=[])  # fmt: skip
    times = review_times(design.session_seconds)
    report = playtest(build, design.checks, design.session_seconds, capture_at=times)
    plain = playtest(build, design.checks, design.session_seconds)
    assert report["passed"] and [s["t"] for s in report["screenshots"]] == pytest.approx(
        times, abs=0.1
    )
    for s in report["screenshots"]:
        image = Image.open(io.BytesIO(s["png"])).convert("RGB")
        assert image.size == (320, 180) and len(image.getcolors(1 << 16)) > 8  # drawn, not blank
    # Drawing a frame does not change the game: same events as a logic-only run.
    assert report["events"] == plain["events"] and "screenshots" not in plain


def test_fix_cap_follows_the_review_cost():
    from spriteforge.game.art_review import fix_cap

    assert fix_cap(4, 0.08, 0.0, 0.01) == 4
    assert fix_cap(4, 0.08, 0.045, 0.01) == 3  # the live review: 3 fixes fit, not 4
    assert fix_cap(4, 0.08, 0.09, 0.01) == 0
    assert fix_cap(4, 0.08, 0.5, 0.0) == 4  # free images: only the count cap


def test_review_cost_cap_leaves_the_rest_as_suggestions(tmp_path, budget):
    design, plan, art, images = fallback_art(budget)
    reply = answer((SLIME, "a", "blocking"), (GEM, "b", "blocking"), (FLOOR, "c", "blocking"))
    outcome = mock_build(tmp_path, design, art, budget)
    cfg = load_config()
    cfg.game.review_cost_cap = 0.02  # two image calls at budget.image_cost 0.01
    reviewed = review_art(models(budget, Reviewer(budget, reply), images), design, plan, art,
                          outcome, cfg, tmp_path / "out", lambda m: None)  # fmt: skip
    assert len(images.calls) == 2
    status = {i["entity"]: i["status"] for i in reviewed.report["issues"]}
    assert status["slime"] == status["gem"] == "fixed"
    assert status["floor"].startswith("not fixed: review cost cap reached ($0.02")
    assert reviewed.report["cost"] == {"review": 0.0, "fixes": 0.02, "cap": 0.02}
