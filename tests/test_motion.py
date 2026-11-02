"""L2.4: the motion design: rows that move like the thing they show, forms, effects (sf.fx)."""

import io
import json
import re

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from test_game_db import conformance
from test_levels import (
    GATED,
    WORDS,
    answer,
    check,
    levels_of,
    read,
    slime_design,
    slime_plan,
    submit,
)

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates
from spriteforge.core.state import Store
from spriteforge.game.art import ArtPick, ArtPlan, MuseOptions, keyword_plan, make_art, resolve_plan
from spriteforge.game.art_review import review_prompt, review_slots
from spriteforge.game.build import build_game
from spriteforge.game.codegen import MOCK_PLATFORMER, assets_summary
from spriteforge.game.gamedb import open_db
from spriteforge.game.levels import levels_script, player_body
from spriteforge.game.motion import (
    AnimationBrief,
    Effect,
    FormLook,
    MotionRow,
    Pose,
    crumble_frames,
    finish_rows,
    make_motion,
    row_problems,
)
from spriteforge.game.pipeline import Models, motion_previews, produce_art, write_motion
from spriteforge.game.playtest import playtest_levels
from spriteforge.game.record import READY, serve, wait_until
from spriteforge.library import add_pack
from spriteforge.memory.okf.validate import validate
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider
from spriteforge.stages.workflow import start_run, workflow_step


@pytest.fixture
def models(tmp_path):
    cfg = load_config()
    store = Store(tmp_path / "s.sqlite3")
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump(), "mock": True})
    return Models(cfg, Budget(store, run.id, cfg.budget), True), cfg


def slime_motion(models):
    records = [check(GATED, n=1)]
    return write_motion(models, slime_design(), slime_plan(), records)


def rows_of(record, entity):
    return {r.name: r for r in record.rows(entity)}


# --- the brief: what each thing does, written so code can hold it to it -------------------------


def test_the_offline_brief_gives_each_thing_the_motion_it_needs(models):
    m = slime_motion(models[0])
    slime = rows_of(m, "slime")
    assert slime["hop"].plays == "move" and len(slime["hop"].frames) == 4
    assert any(f.airborne for f in slime["hop"].frames) and "squash" in slime["hop"].frames[0].shows
    assert {"jump", "dash"} <= set(slime) and not slime["jump"].loop
    forms = {r.form: r for r in m.rows("slime") if r.plays == "form"}
    assert set(forms) == {"Acid Form", "Metal Form"} and all(r.loop for r in forms.values())
    looks = {f.ability: f for f in m.brief.forms}
    assert looks["Acid Form"].tint == "#7CE04A" and "green" in looks["Acid Form"].looks
    assert looks["Metal Form"].tint == "#A8B0BC" and "metal" in looks["Metal Form"].looks
    # what the levels open: the walker dies, the crate and the cracked wall break and end gone
    assert [r.plays for r in m.rows("walker")] == ["idle", "move", "die"]
    assert [r.plays for r in m.rows("crate")] == ["idle", "break"]
    assert [r.plays for r in m.rows("wall")] == ["break"]  # a tile keeps only its break row
    for r in m.rows("walker")[-1:] + m.rows("crate")[-1:] + m.rows("wall"):
        assert not r.loop and "gone" in r.frames[-1].shows
    assert m.rows("ground") == [] and m.rows("coin")[0].plays == "pickup"
    moments = {x.on for x in m.brief.effects}
    assert {"jump", "land", "dash", "break", "kill", "pickup", "hurt", "transform"} <= moments
    jump, land = (next(x for x in m.brief.effects if x.on == on) for on in ("jump", "land"))
    assert jump.squash < 0 < land.squash  # a jump stretches, a landing squashes
    assert m.problems == []


def test_fit_repairs_what_code_can(models):
    design, plan = slime_design(), slime_plan()
    P = Pose
    rows = [
        MotionRow(entity="the player", name="Hop", plays="move", form="", fps=40, loop=True,
                  frames=[P(shows=f"pose {i}", airborne=i == 2) for i in range(6)]),
        MotionRow(entity="crates", name="break", plays="break", form="", fps=10, loop=True,
                  frames=[P(shows="cracks", airborne=False), P(shows="splits", airborne=False)]),
        MotionRow(entity="wall", name="idle", plays="idle", form="", fps=4, loop=True,
                  frames=[P(shows="still", airborne=False)]),
        MotionRow(entity="slime", name="acid", plays="form", form="acid", fps=10, loop=True,
                  frames=[P(shows="green drip", airborne=False)] * 2),
        MotionRow(entity="dragon", name="fly", plays="move", form="", fps=8, loop=True,
                  frames=[P(shows="wings", airborne=True)]),
        MotionRow(entity="slime", name="hop", plays="move", form="", fps=8, loop=True,
                  frames=[P(shows="again", airborne=False)]),
    ]  # fmt: skip
    brief = AnimationBrief(
        rows=rows,
        forms=[FormLook(ability="acid", looks="toxic green", tint="green", particles="#00ff00"),
               FormLook(ability="Fire Form", looks="flames", tint="#FF0000", particles="")],
        effects=[Effect(name="Kill", on="kill", particles=500, colors=["red", "#FFFFFF"], speed=900,
                        flash="#fff", shake=7, squash=3, seconds=0)],
    )  # fmt: skip
    m = make_motion(brief, design, plan, {"crate": "break", "walker": "die"})
    slime = rows_of(m, "slime")
    assert slime["hop"].fps == 20 and len(slime["hop"].frames) == 4  # clamped, trimmed
    assert slime["acid"].form == "Acid Form" and slime["acid"].plays == "form"
    crate = rows_of(m, "crate")["break"]
    assert not crate.loop and crate.frames[-1].shows.startswith("gone")
    assert m.rows("wall") == []  # a tile's idle row is dropped
    assert "dragon" not in {r.entity for r in m.brief.rows}
    looks = {f.ability: f for f in m.brief.forms}
    assert set(looks) == {"Acid Form", "Metal Form"}  # Fire Form is no ability of the plan
    assert looks["Acid Form"].tint == "#3C9C3C"  # 'green' is no hex: the colour its looks name
    assert looks["Acid Form"].particles == "#00FF00"
    kill = next(x for x in m.brief.effects if x.name == "kill")
    assert (kill.particles, kill.speed, kill.shake, kill.squash, kill.seconds) == (30, 300, 1, 0.4, 0.1)
    assert kill.flash == "" and kill.colors == ["#FFFFFF"]
    notes = "\n".join(m.notes)
    for said in ("unknown entity 'dragon'", "second row 'hop'", "a break row plays once",
                 "ends with nothing left", "keeps only break rows", "Fire Form", "no move row",
                 "no row for Metal Form", "a die row added"):  # fmt: skip
        assert said in notes, said


def test_a_design_change_keeps_the_rows_of_what_still_exists(models):
    m = slime_motion(models[0])
    design = slime_design()
    design.entities.append(design.entities[1].model_copy(update={"id": "bat", "look": "a bat"}))

    class Counting(MockLLMProvider):
        calls = 0

        def chat(self, request):
            Counting.calls += 1
            return super().chat(request)

    b, cfg = models[0].budget, models[1]
    part = AnimationBrief(rows=[MotionRow(entity="bat", name="flap", plays="move", form="", fps=12,
                                          loop=True, frames=[Pose(shows="wings up", airborne=True),
                                                             Pose(shows="wings down", airborne=True)])],
                          forms=[], effects=[])  # fmt: skip
    again = write_motion(Models(cfg, b, True, provider=Counting(b, [part.model_dump()])), design,
                         slime_plan(), [check(GATED, n=1)], m)  # fmt: skip
    assert Counting.calls == 1 and [r.name for r in again.rows("bat")][:2] == ["idle", "flap"]
    assert again.rows("slime") == m.rows("slime")  # its locked art keeps its rows
    same = write_motion(Models(cfg, b, True, provider=Counting(b, [])), slime_design(),
                        slime_plan(), [check(GATED, n=1)], m)  # fmt: skip
    assert Counting.calls == 1 and same.brief.rows == m.brief.rows  # nothing new: no call


# --- frames: checked and repaired by code --------------------------------------------------------


def blob(w=16, h=12, size=(20, 20), lift=0, color=(90, 200, 90, 255)) -> Image.Image:
    img = Image.new("RGBA", size)
    ImageDraw.Draw(img).ellipse(((size[0] - w) // 2, size[1] - h - lift, (size[0] + w) // 2 - 1,
                                 size[1] - 1 - lift), fill=color, outline=(20, 20, 30, 255))  # fmt: skip
    return img


def row(name, plays, n, loop=True, air=()):
    return MotionRow(entity="slime", name=name, plays=plays, form="", fps=10, loop=loop,
                     frames=[Pose(shows=f"{name} {i}", airborne=i in air) for i in range(n)])  # fmt: skip


def test_row_checks_find_what_slides_jumps_or_lingers_and_code_repairs_it():
    hop = row("hop", "move", 4)
    still = [blob()] * 4
    assert any("same picture" in p for p in row_problems(still, hop, "player"))
    frames, checks = finish_rows(still, [hop], "player", [4])
    assert checks[0].repaired == ["squash and stretch made by code from its first frame"]
    assert len({f.tobytes() for f in frames}) >= 3  # it moves now
    bottoms = {f.getchannel("A").getbbox()[3] for f in frames}
    assert bottoms == {20}  # on one ground line
    moving = [blob(16, 12), blob(18, 10), blob(12, 16), blob(16, 12)]
    assert row_problems(moving, hop, "player") == []
    giant = [blob(16, 12), blob(4, 3), blob(12, 16), blob(16, 12)]
    assert any("change the subject's size" in p for p in row_problems(giant, hop, "player"))
    floating = [blob(16, 12), blob(18, 10, lift=4), blob(12, 16), blob(16, 12)]
    assert any("ground line" in p for p in row_problems(floating, hop, "player"))
    air = row("hop", "move", 4, air=(1,))  # an airborne frame may leave the ground
    assert not any("ground line" in p for p in row_problems(floating, air, "player"))
    jumpy = [blob(6, 6), blob(9, 8), blob(12, 10), blob(16, 12)]  # grows, then snaps back
    assert any("loop jumps" in p for p in row_problems(jumpy, hop, "player"))
    brk = row("break", "break", 4, loop=False)
    crate = [blob(16, 16, color=(140, 100, 60, 255))] * 4
    assert any("does not end empty" in p for p in row_problems(crate, brk, "prop"))
    frames, checks = finish_rows(crate, [brk], "prop", [4])
    assert not frames[-1].getbbox() and checks[0].repaired == ["the last frame emptied by code"]
    crumble = crumble_frames(blob(16, 16), 4)
    assert len(crumble) == 4 and not crumble[-1].getbbox() and crumble[1].getbbox()


# --- the art: Muse draws the rows, the manifest says how to play them ----------------------------


class Recorder(MockImageProvider):
    def __init__(self, budget, still=False):
        super().__init__(budget)
        self.requests, self.still = [], still

    def generate(self, request):
        self.requests.append(request)
        if not self.still or "columns by" not in request.prompt:
            return super().generate(request)
        # the same pose in every cell: a row that would slide
        cols, rows = map(int, re.search(r"(\d+) columns by (\d+) rows", request.prompt).groups())
        sheet = Image.new("RGBA", request.size)
        cw, ch = request.size[0] // cols, request.size[1] // rows
        for i in range(cols * rows):
            x, y = (i % cols) * cw, (i // cols) * ch
            ImageDraw.Draw(sheet).rectangle((x + cw // 4, y + ch // 3, x + 3 * cw // 4, y + ch - 8),
                                            fill=(90, 200, 90, 255))  # fmt: skip
        out = io.BytesIO()
        sheet.save(out, format="PNG")
        from spriteforge.providers.base import ImageResult

        return ImageResult(data=out.getvalue(), metadata={"provider": "test"})


def test_muse_draws_the_rows_and_the_manifest_says_how_to_play_them(models):
    model, cfg = models
    m = slime_motion(model)
    images = Recorder(model.budget)
    art = produce_art(Models(cfg, model.budget, True, images=images), slime_design(),
                      keyword_plan(slime_design()), cfg, motion=m)  # fmt: skip
    prompt = next(r.prompt for r in images.requests if "a slime" in r.prompt and "columns by" in r.prompt)
    assert "Row 2 (hop): 5. hop: squashed low and wide" in prompt and "off the ground" in prompt
    assert "Row 4 (acid form, in Acid Form: bright green, glossy, dripping acid)" in prompt
    assert "a row marked as a form changes only its colours" in prompt
    manifest = art.manifest()
    slime = manifest["sprites"]["slime"]
    assert list(slime["rows"]) == ["idle", "hop", "jump", "acid_form", "metal_form", "dash"]
    hop = slime["rows"]["hop"]
    assert (hop["plays"], hop["fps"], hop["loop"]) == ("move", 10, True)
    assert hop["lift"][2] > 0 and hop["lift"][0] == 0  # the airborne frame is shown up in the air
    assert slime["anims"]["jump"][1] == 2 and slime["rows"]["jump"]["loop"] is False
    assert manifest["forms"] == {"Acid Form": {"row": "acid_form", "tint": "#7CE04A", "particles": "#7CE04A"},
                                 "Metal Form": {"row": "metal_form", "tint": "#A8B0BC", "particles": "#A8B0BC"}}
    assert {"jump", "land", "kill", "break", "pickup", "transform"} <= {e["on"] for e in manifest["fx"].values()}
    assert "wall" in manifest["images"] and manifest["sprites"]["wall_break"]["anims"]["break"] == [0, 4]
    walker = manifest["sprites"]["walker"]
    assert walker["rows"]["die"]["loop"] is False
    strip = Image.open(io.BytesIO(art.filled["walker"].png))
    start, n = walker["anims"]["die"]
    w, h = walker["frame"]
    assert not strip.crop(((start + n - 1) * w, 0, (start + n) * w, h)).getbbox()  # ends empty
    summary = assets_summary(manifest)
    assert "hop (4 frames, move, loop)" in summary and "acid_form (4 frames, form Acid Form, loop)" in summary
    assert "Effects (sf.fx" in summary and "Acid Form (acid_form)" in summary
    previews = motion_previews(art)
    gif = Image.open(io.BytesIO(previews["motion.gif"]))
    assert gif.format == "GIF" and gif.n_frames > 10
    assert Image.open(io.BytesIO(previews["motion.png"])).height > 400


def test_a_row_drawn_still_is_drawn_again_then_made_to_move_by_code(models):
    model, _ = models
    m = slime_motion(model)
    design = slime_design()
    design.entities = [e for e in design.entities if e.id in {"slime", "ground"}]
    images = Recorder(model.budget, still=True)
    gen = lambda request, edit: images.generate(request).data
    art = make_art(design, keyword_plan(design), generate=gen, motion=m,
                   options=MuseOptions(retries=1, workers=1))  # fmt: skip
    sheets = [r for r in images.requests if "columns by" in r.prompt]
    assert len(sheets) == 2 and "the hop row: its 4 frames are the same picture" in sheets[1].prompt
    slime = art.filled["slime"]
    hop = next(c for c in slime.checks if c.row == "hop")
    assert hop.repaired == ["squash and stretch made by code from its first frame"]
    assert any("hop: squash and stretch made by code" in r for r in slime.reasons)
    assert "repaired: squash and stretch" in review_prompt(design, art, review_slots(art), [], 0, rows=True)


def test_locked_rows_are_reused_without_a_call(models):
    model, cfg = models
    m = slime_motion(model)
    design = slime_design()
    first = produce_art(model, design, keyword_plan(design), cfg, motion=m)
    old = {eid: {**f.record, "png": f.png, "version": 1} for eid, f in first.filled.items() if f.record}
    images = Recorder(model.budget)
    again = produce_art(Models(cfg, model.budget, True, images=images), design, keyword_plan(design),
                        cfg, lambda eid: old.get(eid), motion=m)  # fmt: skip
    assert images.requests == []
    assert again.filled["slime"].source == "muse (locked, from v1)"
    assert again.manifest()["sprites"]["slime"] == {**first.manifest()["sprites"]["slime"],
                                                    "source": "muse (locked, from v1)"}  # fmt: skip


def test_a_library_actor_that_cannot_show_the_rows_is_drawn_by_muse(tmp_path, monkeypatch, models):
    lib = tmp_path / "library"
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    src = tmp_path / "pack"
    for name, frames in (("Run", 4), ("Idle", 2), ("Jump", 2)):
        img = Image.new("RGBA", (frames * 32, 32))
        for i in range(frames):
            ImageDraw.Draw(img).ellipse((i * 32 + 6, 12, i * 32 + 26, 31), fill=(90, 200, 90, 255))
        (src / "Mobs" / "Slime").mkdir(parents=True, exist_ok=True)
        img.save(src / "Mobs" / "Slime" / f"{name}.png")
    add_pack(src, "side", category="enemy", cell="auto", name_depth=2,
             tags=["side-view"], license="CC0", license_verified=True, root=lib)  # fmt: skip
    design = slime_design()
    pick = ArtPick(entity="slime", source="actor", asset="side/slime", prompt="", reference="",
                   recolor="", reason="the library has a slime")  # fmt: skip
    plan = ArtPlan(variant="normal", picks=[pick])
    kept, _ = resolve_plan(plan, design)
    assert kept["slime"].source == "actor"  # without a motion design the actor stands
    m = slime_motion(models[0])
    picks, warnings = resolve_plan(plan, design, m)
    assert picks["slime"].source == "muse"
    assert any("cannot show the motion design's acid_form, metal_form row(s)" in w for w in warnings)
    # an actor that can show every row keeps them, cut into the motion design's rows
    walker = make_motion(AnimationBrief(rows=[], forms=[], effects=[]), design, slime_plan(),
                         {"walker": "die"})  # fmt: skip
    art = make_art(design, ArtPlan(variant="normal", picks=[
        ArtPick(entity="walker", source="actor", asset="side/slime", prompt="", reference="",
                recolor="", reason="test")]), generate=None, motion=walker)  # fmt: skip
    meta = art.filled["walker"].meta
    assert art.filled["walker"].source == "library side/slime"
    assert list(meta["rows"]) == ["idle", "walk", "die"] and meta["anims"]["walk"][1] == 4
    die = next(c for c in art.filled["walker"].checks if c.row == "die")
    assert die.repaired == ["the last frame emptied by code"] or die.problems == []


# --- the SDK plays the rows, the forms and the effects ------------------------------------------


def built(tmp_path, models, records, drop_form_row="", fx=None):
    model, cfg = models
    design, plan = slime_design(), slime_plan()
    m = write_motion(model, design, plan, records)
    art = produce_art(model, design, keyword_plan(design), cfg, motion=m)
    manifest = art.manifest()
    if drop_form_row:  # as if that row had failed its checks: the SDK tints instead
        manifest["forms"][drop_form_row]["row"] = ""
    if fx is not None:
        manifest["fx"] = fx
    script = levels_script(records, design, plan, player_body(design, manifest))
    return build_game(tmp_path / "build", title="t", controls="c",
                      files={"game.js": MOCK_PLATFORMER.read_text(encoding="utf-8")},
                      assets=manifest, images=art.files(), credits=[], levels=script)  # fmt: skip


WATCH = """() => {
  const L = sf.level.current, p = L && L.player;
  if (!p) return null;
  const a = p.anims.currentAnim;
  return {anim: a ? a.key.split(':').pop() : '', form: sf.state.form || '', sy: L.skin.scaleY,
          lift: Math.round(L.skin.y - (p.y + p.displayHeight / 2)), tint: L.skin.isTinted ? L.skin.tintTopLeft : -1,
          drawn: p.willRender(sf.game.scene.getScenes(true)[0].cameras.main)};
}"""


def watch(build, steps=400):
    seen = []
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{url}/index.html?autoplay=1&headless=1&god=1&only=1&level=1")
        assert wait_until(page, READY)
        for _ in range(steps):
            page.evaluate("window.sfRun(0.1, 30, true)")
            if (s := page.evaluate(WATCH)) is not None:
                seen.append(s)
            if page.evaluate("!!window.sf.counts.level_complete"):
                break
        page.evaluate("window.sfRun(2.5, 30, true)")  # the last break rows play out
        result = page.evaluate("""() => ({fx: sf.fxCount, counts: Object.fromEntries(
            Object.entries(sf.counts).map(([k, v]) => [k, v.count])), errors: sf.errors,
            gone: sf.level.current.list.filter(t => t.open && t.kind !== 'pickup')
                  .map(t => ({id: t.id, active: t.sprites.some(o => o.active)})),
            breakTexture: sf.has('wall_break')})""")  # fmt: skip
        browser.close()
    return seen, result


def test_the_sdk_plays_the_rows_the_forms_and_the_effects(tmp_path, models):
    records = [check(GATED, n=1, events=["crate_open", "enemy_defeat", "wall_break", "level_complete"])]
    seen, out = watch(built(tmp_path, models, records))
    assert out["errors"] == [] and out["counts"].get("level_complete") == 1, out
    anims = {s["anim"] for s in seen}
    # the hop before any form; while a form lasts its row is shown for every move (jumps too)
    assert "hop" in anims and {"acid_form", "metal_form"} <= anims
    assert {"Acid Form", "Metal Form"} <= {s["form"] for s in seen}
    assert any(s["sy"] < 0.95 or s["sy"] > 1.05 for s in seen)  # squash and stretch on the skin
    assert not any(s["drawn"] for s in seen)  # the physics body is not drawn; its skin is
    assert any(s["lift"] < 0 for s in seen if s["anim"] in {"hop", "acid_form", "metal_form"})
    fx = out["fx"]
    for name in ("jump", "land", "dash", "break", "kill", "transform"):
        assert fx.get(name, 0) >= 1, (name, fx)
    assert out["counts"].get("form", 0) >= 2
    assert out["breakTexture"] and out["gone"] and not any(t["active"] for t in out["gone"]), out["gone"]


def test_a_form_without_its_row_is_a_tint_with_particles(tmp_path, models):
    records = [check(GATED, n=1, events=["crate_open", "enemy_defeat", "wall_break", "level_complete"])]
    seen, out = watch(built(tmp_path, models, records, drop_form_row="Metal Form"))
    assert out["errors"] == [] and out["counts"].get("level_complete") == 1
    metal = [s for s in seen if s["form"] == "Metal Form"]
    assert metal and all(s["anim"] != "metal_form" for s in metal)
    assert any(s["tint"] == 0xA8B0BC for s in metal)
    acid = [s for s in seen if s["form"] == "Acid Form"]
    assert acid and all(s["tint"] in (-1, 0xFFFFFF) or s["anim"] == "acid_form" for s in acid)


def test_the_bot_still_clears_the_level_with_the_motion_design(tmp_path, models):
    records = [check(GATED, n=1, events=["crate_open", "enemy_defeat", "wall_break", "level_complete"])]
    report = playtest_levels(built(tmp_path, models, records), [], levels_of(records))
    level = report["levels"][0]
    assert level["cleared"] and level["missing"] == [] and report["errors"] == [], level


# --- the gated run: the motion design per design version, seen at G4, in the bundle -------------


def test_a_gated_run_designs_motion_once_and_shows_it_at_g4(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.workspace = tmp_path
    ident = start_run(store, cfg, mock=True, goal=WORDS).id
    workflow_step(store, ident)
    answer(store, ident, form={"genre": "genres/side-scrolling-platformer"})
    answer(store, ident, form={"perspective": "side-scroll"})
    answer(store, ident)
    submit(store, ident)  # approve the design: levels, motion, art, code, playtests
    gate = Gates(store).pending(ident)
    names = {a.id: a for a in store.artifacts(ident)}
    shown = [names[i].name for i in gate.artifacts_to_show]
    assert "game/v1/motion.gif" in shown and names[next(i for i in gate.artifacts_to_show
                                                        if names[i].name == "game/v1/motion.gif")].mime == "image/gif"  # fmt: skip
    motion = read(store, ident, "game/design/v1-motion.json")
    player = next(r for r in motion["brief"]["rows"] if r["plays"] == "move")
    assert len(player["frames"]) >= 2
    report = read(store, ident, "game/v1/report.json")
    assert report["passed"] and report["motion"]["rows"] and report["motion"]["effects"]
    db = open_db(tmp_path / "runs" / ident / "game" / "db", store, ident)
    entries = {e.id: e for e in db.scan()[0]}
    assert entries["art/motion"].metadata.resource == "/references/art/motion.json"
    assert (db.root / "references" / "art" / "motion.gif").exists()
    assert "# Schema" in entries["art/motion"].body and "/art/motion.md" in entries["art/style"].body
    assert validate(db, external=False).errors == [] and conformance(db) == []
    # feedback on one level: the same motion design, the art locked
    submit(store, ident, "Make it longer", form={"level": "Level 2: Level 2"})
    names = [a.name for a in store.artifacts(ident)]
    assert not any(re.fullmatch(r"game/design/v\d+-motion\.json", n) and n != "game/design/v1-motion.json"
                   for n in names)  # fmt: skip
    v2 = read(store, ident, "game/v2/report.json")
    assert all(a["source"].startswith(("muse (locked", "library", "tileset")) for a in v2["art"]), v2["art"]
    assert json.dumps(v2["motion"]["forms"]) == json.dumps(report["motion"]["forms"])


def test_only_abilities_that_change_the_look_are_forms():
    """Live 2026-11-02: 'Dissolve: touch dissolves wooden crates to spawn coins or a form' got a
    form row from code, and the slime's dash row no longer fitted on its sheet."""
    from spriteforge.game.motion import is_form_ability

    assert is_form_ability("Acid Form", "kills enemies on touch")
    assert is_form_ability("Shadow", "the slime transforms into a shadow")
    assert not is_form_ability("Dissolve", "touch dissolves wooden crates to spawn coins or a form")
    assert not is_form_ability("Dissolve", "opens crates that give a transformation")
    plan = slime_plan()
    plan.plan.abilities[0].does = "touch dissolves wooden crates to spawn coins or a form"
    m = make_motion(AnimationBrief(rows=[], forms=[], effects=[]), slime_design(), plan)
    assert [r.name for r in m.rows("slime")] == ["idle", "hop", "jump", "acid_form", "metal_form", "dash"]
    assert {f.ability for f in m.brief.forms} == {"Acid Form", "Metal Form"}


def test_one_effect_per_moment_the_one_that_fits_what_happened(tmp_path, models):
    """Live 2026-11-02: acid_transform and metal_transform were both on 'transform', and a
    crate's dissolve and a wall's break both on 'break': every transform played both colours
    and the wall played the crate's dissolve too."""
    fx = {"acid_transform": {"on": "transform", "particles": 4, "colors": ["#39FF5A"], "speed": 40,
                             "flash": "", "shake": 0, "squash": 0, "seconds": 0.2},
          "metal_transform": {"on": "transform", "particles": 4, "colors": ["#DDE6F0"], "speed": 40,
                              "flash": "", "shake": 0, "squash": 0, "seconds": 0.2},
          "dissolve": {"on": "break", "particles": 4, "colors": ["#8A5A2B"], "speed": 40,
                       "flash": "", "shake": 0, "squash": 0, "seconds": 0.2},
          "break": {"on": "break", "particles": 4, "colors": ["#8B8F9A"], "speed": 40,
                    "flash": "", "shake": 0, "squash": 0, "seconds": 0.2}}  # fmt: skip
    records = [check(GATED, n=1, events=["crate_open", "enemy_defeat", "wall_break", "level_complete"])]
    _, out = watch(built(tmp_path, models, records, fx=fx))
    assert out["errors"] == [] and out["counts"].get("level_complete") == 1
    # acid, then metal (taking metal drops acid): each transform plays its own effect once
    assert out["fx"].get("acid_transform") == 1 and out["fx"].get("metal_transform") == 1, out["fx"]
    # three crates dissolve; the wall (one thing of 7 cells: its first and last cell) breaks
    assert out["fx"].get("dissolve") == 3 and out["fx"].get("break") == 2, out["fx"]
