"""L2.3: levels as data, checked by code with the plan's movement and played by the SDK's bot."""

import io
import json

import pytest
from PIL import Image
from test_game_db import conformance

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan
from spriteforge.game.build import build_game
from spriteforge.game.codegen import MOCK_PLATFORMER
from spriteforge.game.design import Check, Entity, GameDesign, fallback_design
from spriteforge.game.gamedb import open_db
from spriteforge.game.level_gen import fallback_level
from spriteforge.game.level_preview import PX, preview
from spriteforge.game.levels import (
    ChallengeAt,
    LegendEntry,
    LevelSpec,
    evaluate,
    fit_level,
    levels_script,
    player_body,
)
from spriteforge.game.pipeline import Models, produce_art, write_level
from spriteforge.game.plan import (
    Ability,
    Challenge,
    GamePlan,
    LevelPlan,
    Mechanic,
    Movement,
    fallback_plan,
    make_record,
)
from spriteforge.game.playtest import playtest_levels
from spriteforge.game.reach import Grid, Motion, Thing, gap_limits, solve
from spriteforge.game.revision import fallback_revision
from spriteforge.memory.okf.validate import validate
from spriteforge.providers.mock import MockLLMProvider
from spriteforge.stages.workflow import start_run, workflow_step

# --- a slime-like game: crates, acid kills enemies, metal breaks walls ---------------------------

WORDS = ("A side-view pixel platformer with 3 levels. The slime dissolves crates; acid beats "
         "enemies; metal breaks cracked walls.")  # fmt: skip


def slime_design() -> GameDesign:
    E = Entity
    ents = [E(id="slime", role="player", look="a slime", behaviour="moves", width=12, height=10, animations=[]),
            E(id="walker", role="enemy", look="a beetle", behaviour="patrols", width=14, height=10, animations=[]),
            E(id="crate", role="prop", look="a crate", behaviour="dissolves", width=16, height=16, animations=[]),
            E(id="wall", role="tile", look="cracked stone", behaviour="breaks", width=16, height=16, animations=[]),
            E(id="ground", role="tile", look="grass", behaviour="floor", width=16, height=16, animations=[]),
            E(id="spikes", role="tile", look="spikes", behaviour="hurt", width=16, height=16, animations=[]),
            E(id="coin", role="pickup", look="a coin", behaviour="score", width=8, height=8, animations=[]),
            E(id="acid", role="pickup", look="green vial", behaviour="acid form", width=12, height=12, animations=[]),
            E(id="metal", role="pickup", look="grey ingot", behaviour="metal form", width=12, height=12, animations=[]),
            E(id="flag", role="prop", look="a flag", behaviour="checkpoint", width=12, height=16, animations=[]),
            E(id="door", role="prop", look="a door", behaviour="exit", width=16, height=24, animations=[])]  # fmt: skip
    checks = [Check(id=e, description=e, event=e, min_count=1, by_second=0)
              for e in ("jump", "crate_open", "enemy_defeat", "wall_break", "level_complete", "win")]  # fmt: skip
    return GameDesign(title="Slime", pitch="p", camera="side-view", controls="c", session_seconds=120,
                      player="slime", world="w", look="l", entities=ents, rules=["r"], progression=[],
                      difficulty=["d"], win="exit", lose="", hud=[], checks=checks)  # fmt: skip


def slime_plan():
    plan = GamePlan(
        requirements=[], style="s",
        movement=Movement(move_speed=6, jump_height=4, air_time=0.6, dash_distance=4.5,
                          dash_cooldown=0.8, coyote_time=0.1),
        abilities=[Ability(name="Dissolve", does="opens crates", numbers="", unlocked_by="start"),
                   Ability(name="Acid Form", does="beats enemies", numbers="", unlocked_by="acid crate"),
                   Ability(name="Metal Form", does="breaks walls", numbers="", unlocked_by="metal crate")],
        mechanics=[Mechanic(name="Transform Swap", what="a new form replaces the old", purpose="routing")],
        challenges=[Challenge(name="Small Gap", tests="jump a gap", needs=[]),
                    Challenge(name="Enemy Corridor", tests="beat or avoid", needs=["Acid Form"]),
                    Challenge(name="Cracked Wall Gate", tests="break the wall", needs=["Metal Form"])],
        levels=[LevelPlan(n=1, name="Crates", teaching_goal="dissolve", new_idea="crates", challenges=["Small Gap"],
                          abilities=["Dissolve"], difficulty=2),
                LevelPlan(n=2, name="Acid", teaching_goal="acid kills", new_idea="acid", challenges=["Enemy Corridor"],
                          abilities=["Dissolve", "Acid Form"], difficulty=5),
                LevelPlan(n=3, name="Metal", teaching_goal="metal breaks", new_idea="metal",
                          challenges=["Enemy Corridor", "Cracked Wall Gate"],
                          abilities=["Dissolve", "Acid Form", "Metal Form"], difficulty=7)],
        feel=[], animations=[], coverage=[], risks=[])  # fmt: skip
    return make_record(plan, "")


def L(ch, kind, ent, needs="", by="never", holds="", gives=""):
    return LegendEntry(char=ch, kind=kind, entity=ent, needs=needs, opened_by=by, holds=holds, gives=gives)


LEGEND = [L("#", "solid", "ground"), L("=", "one_way", "ground"), L("^", "hazard", "spikes"),
          L("@", "start", "slime"), L("c", "crate", "crate", "Dissolve", "touch", "coin"),
          L("a", "crate", "crate", "Dissolve", "touch", "acid", "Acid Form"),
          L("m", "crate", "crate", "Dissolve", "touch", "metal", "Metal Form"),
          L("W", "breakable", "wall", "Metal Form", "dash"),
          L("e", "enemy", "walker", "Acid Form", "touch"), L("o", "pickup", "coin"),
          L("F", "checkpoint", "flag"), L("D", "exit", "door")]  # fmt: skip

# Level 3: acid beats the enemy in a low corridor (no room to jump it), metal breaks a wall that
# reaches the ceiling. The metal crate comes after the acid, and taking metal drops acid.
def gated_map() -> list[str]:
    width = 52
    row = lambda fill, **at: "".join(at.get(f"c{c}", fill) for c in range(width))
    middle = row(" ", c0="#", c33="#", c49="W", c51="#")
    floor = row(" ", c0="#", c1="@", c4="c", c7="a", c12="^", c13="^", c20="F", c30="e", c39="m",
                c42="F", c49="W", c51="D")  # fmt: skip
    return [row("#")] + [middle] * 6 + [floor, row("#"), row("#")]


GATED = gated_map()


def spec(rows, n=3, **kw) -> LevelSpec:
    values = {"n": n, "name": f"Level {n}", "legend": LEGEND, "rows": rows,
              "challenges": [ChallengeAt(name="Enemy Corridor", column=28),
                             ChallengeAt(name="Cracked Wall Gate", column=48)],
              "events": ["jump", "crate_open", "enemy_defeat", "wall_break", "level_complete"],
              "exclusive": ["Acid Form", "Metal Form"], "notes": "test"} | kw  # fmt: skip
    return LevelSpec(**values)


def check(rows, n=3, previous=None, **kw):
    design, plan = slime_design(), slime_plan()
    return evaluate(spec(rows, n, **kw), n, design, plan, previous, player_body(design))


# --- the model of the player's movement ---------------------------------------------------------


def strip(rows, things=()):
    tiles, out, start = {}, [], (0, 0)
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch == "#":
                tiles[(c, r)] = "solid"
            elif ch == "^":
                tiles[(c, r)] = "hazard"
            elif ch == "@":
                start = (c, r)
            elif ch == "D":
                out.append(Thing("x", "exit", "door", ((c, r),)))
    return Grid(len(rows[0]), len(rows), tiles, out + list(things), start)


def test_jumps_reach_what_the_plan_says_and_no_further():
    plan = slime_plan()  # jump 4 tiles in 0.6 s, 6 tiles/s, dash 4.5 tiles
    k = plan.kinematics
    m = Motion(speed=6, gravity=k.gravity / 16, jump=4, body_w=0.75, body_h=0.625)
    gap = lambda n, dash=0: solve(strip(["." * 30, "@" + "." * 26 + "D..",
                                         "#" * 10 + "." * n + "#" * (20 - n)]),
                                  Motion(speed=6, gravity=k.gravity / 16, jump=4, dash=dash,
                                         body_w=0.75, body_h=0.625)).solved  # fmt: skip
    assert gap(k.max_gap) and not gap(k.max_gap + 1)
    assert gap(k.max_gap_with_dash, 4.5) and not gap(k.max_gap_with_dash + 1, 4.5)
    dash = Motion(speed=6, gravity=k.gravity / 16, jump=4, dash=4.5, body_w=0.75, body_h=0.625)
    assert gap_limits(dash) == (k.max_gap, k.max_gap_with_dash)  # what the Level Designer is told

    def ledge(up):
        rows = ["." * 20] * 12
        rows[10 - up] = "." * 10 + "#" * 4 + "." * 6
        rows[9 - up] = "." * 11 + "D" + "." * 8
        rows[9], rows[10] = "@" + "." * 19, "#" * 20
        return solve(strip(rows), m).solved

    assert ledge(k.max_rise) and not ledge(k.max_rise + 1)
    # Spikes right beside the take-off: rise first, then move over them.
    assert solve(strip(["." * 20, "@" + "." * 7 + "^^" + "." * 8 + "D.", "#" * 20]), m).solved


# --- code's checks and repairs ------------------------------------------------------------------


def test_a_gated_level_is_solved_with_its_keys_in_order():
    rec = check(GATED)
    assert rec.solved and rec.problems == [], rec.problems
    steps = [(s["do"], s.get("thing", "")) for s in rec.route if s["do"] not in {"walk", "jump", "fall"}]
    order = [d for d, _ in steps]
    # acid taken before the enemy is beaten, metal after it, the wall dashed, then the exit
    assert order.index("touch") < order.index("dash") and order[-1] == "exit"
    kinds = {t: k for k, t in ((s["do"], s.get("thing")) for s in rec.route)}
    assert "dash" in kinds.values()
    assert [k for _, k in rec.features].count("lock") >= 2


def test_a_lock_before_its_key_says_so():
    w = GATED[1].index("W")
    rows = [r.replace("m", " ") for r in GATED]
    rows[7] = rows[7][: w + 1] + "m" + rows[7][w + 2:]  # the metal crate behind the wall
    rec = check(rows)
    assert not rec.solved
    assert any(f"breakable wall (wall) at column {w} blocks the way and needs Metal Form" in p
               and "no Metal Form can be picked up before it" in p for p in rec.problems)  # fmt: skip


def test_a_gate_the_player_can_jump_over_is_no_gate():
    w = GATED[1].index("W")  # the wall reaches the ceiling; cut it to 3 tiles with sky above
    rows = [r[:w] + "   " + r[w + 3:] if i <= 4 else r for i, r in enumerate(GATED)]
    rec = check(rows)
    assert rec.solved
    assert any("the exit can be reached without Metal Form" in p for p in rec.problems)


def test_an_unreachable_slab_is_lowered_by_code():
    rows = ["                              ", "                              ",
            "          =====               ", "            o                 ",
            "                              ", "                              ",
            "                              ", "                              ",
            "@    c        F           D   ", "##############################"]  # fmt: skip
    rows[2], rows[3] = "           o                  ", "          =====               "
    rec = check(rows, n=1, challenges=[ChallengeAt(name="Small Gap", column=5)])
    assert any("lowered" in n for n in rec.notes), rec.notes
    assert not any("cannot be reached" in p for p in rec.problems), rec.problems
    slab = next(r for r, row in enumerate(rec.spec.rows) if "=" in row)
    assert 8 - slab <= 4  # within the 4-tile jump of the floor at row 8


def test_checks_find_a_dull_stretch_and_a_level_easier_than_the_last():
    first = check(GATED)
    flat = ["                                        "] * 6 + [
        "@     c         F                     D ", "########################################",
        "########################################"]  # fmt: skip
    rec = check(flat, n=3, previous=first)
    assert any("between checkpoints) have no challenge" in p for p in rec.problems)
    assert any("measures easier than level 2" in p for p in rec.problems)
    assert any("never uses Metal Form" in p for p in rec.problems)


def test_a_dash_challenge_needs_a_gap_only_a_dash_crosses():
    """Live 2026-11-02: the plan's "Dash Gap" became a 3-tile gap a plain jump clears."""
    design, plan = slime_design(), slime_plan()
    p = plan.plan.model_copy(deep=True)
    p.challenges.append(Challenge(name="Dash Gap", tests="dash over a wide gap", needs=[]))
    p.levels[0].challenges.append("Dash Gap")
    plan = make_record(p, "")

    def level(gap):
        rows = [" " * 40] * 6 + ["@  c                  F            D    ",
                                 "#" * 12 + " " * gap + "#" * (28 - gap), "#" * 12 + " " * gap + "#" * (28 - gap)]
        s = spec(rows, n=1, challenges=[ChallengeAt(name="Small Gap", column=12),
                                        ChallengeAt(name="Dash Gap", column=12)])  # fmt: skip
        return evaluate(s, 1, design, plan, None, player_body(design))

    jumped = level(plan.kinematics.max_gap)
    assert jumped.solved and any("asks for a dash" in x for x in jumped.problems)
    dashed = level(plan.kinematics.max_gap + 2)
    assert dashed.solved and not any("asks for a dash" in x for x in dashed.problems)
    assert any(s.get("dash", -1) >= 0 for s in dashed.route)


def test_fit_repairs_what_code_can():
    design, plan = slime_design(), slime_plan()
    legend = [L("#", "solid", "ground"), L("x", "crate", "crates", "dissolve", "touch", "acid", "acid"),
              L("Q", "pickup", "nothing_like_it"), L("@", "start", "wrong_id")]  # fmt: skip
    rows = ["          ", "    x     ", "@ Z       ", "##########", "####"]
    fitted, notes = fit_level(LevelSpec(n=9, name="", legend=legend, rows=rows, challenges=[],
                                        events=["jump", "not_a_check"], exclusive=["acid"],
                                        notes=""), 2, design, plan)  # fmt: skip
    text = " | ".join(notes)
    assert fitted.n == 2 and fitted.name == "Level 2"
    assert {len(r) for r in fitted.rows} == {10} and "padded" in text
    assert "'Z'" in text and "unknown entity 'nothing_like_it'" in text
    crate = next(e for e in fitted.legend if e.char == "x")
    assert (crate.entity, crate.needs, crate.gives) == ("crate", "Dissolve", "Acid Form")
    assert "entity 'crates' read as 'crate'" in text
    assert fitted.exclusive == ["Acid Form"] and fitted.events == ["jump"]
    assert fitted.rows[2][4] == "x" and "dropped" in text  # the floating crate is on the floor
    assert any(e.kind == "exit" for e in fitted.legend) and "no exit: placed" in text
    assert next(e for e in fitted.legend if e.kind == "start").entity == "slime"


# --- the Level Designer --------------------------------------------------------------------------


@pytest.fixture
def budget(tmp_path):
    cfg = load_config()
    store = Store(tmp_path / "s.sqlite3")
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump(), "mock": True})
    return Budget(store, run.id, cfg.budget), cfg


class Recorded(MockLLMProvider):
    def __init__(self, budget, responses):
        super().__init__(budget, responses)
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return super().chat(request)


def test_problems_go_back_to_the_level_designer_once(budget):
    b, cfg = budget
    design, plan = slime_design(), slime_plan()
    pit = GATED[1].index("W") - 2  # a pit ten tiles wide before the wall: no jump or dash crosses it
    broken = spec(GATED[:-2] + [r[: pit - 10] + " " * 10 + r[pit:] for r in GATED[-2:]])
    fixed = spec(GATED)
    provider = Recorded(b, [broken.model_dump(), fixed.model_dump()])
    models = Models(cfg, b, True, provider=provider)
    rec = write_level(models, 3, design, plan, [])
    assert rec.retried and rec.solved and rec.problems == []
    assert len(provider.requests) == 2
    retry = provider.requests[1].messages[-1].content
    assert "had these problems" in retry and "exit" in retry.lower()


def test_the_offline_levels_hold_and_get_harder():
    plan = make_record(fallback_plan([WORDS], True), WORDS)
    design = fallback_design(WORDS, platformer=True)
    previous, scores = None, []
    for n in (1, 2, 3):
        rec = evaluate(fallback_level(n, design, plan), n, design, plan, previous, player_body(design))
        assert rec.solved and rec.problems == [], (n, rec.problems)
        scores.append(rec.difficulty)
        previous = rec
    assert scores == sorted(scores) and scores[0] < scores[-1]


def test_the_director_sends_level_feedback_to_that_level():
    rev = fallback_revision(slime_design(), "Level 2 is too long and level 3 needs more enemies")
    assert [c.level for c in rev.level_changes] == [2, 3] and not rev.code_instructions


def test_the_map_preview_is_drawn_from_the_grid():
    rec = check(GATED)
    img = Image.open(io.BytesIO(preview(rec, slime_design())))
    assert img.size == (len(GATED[0]) * PX, (len(GATED) + 1) * PX)


# --- the SDK plays the levels --------------------------------------------------------------------


def built(tmp_path, design, plan, records, code=None):
    cfg = load_config()
    store = Store(tmp_path / "art.sqlite3")
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump(), "mock": True})
    models = Models(cfg, Budget(store, run.id, cfg.budget), True)
    art = produce_art(models, design, keyword_plan(design), cfg)
    manifest = art.manifest()
    script = levels_script(records, design, plan, player_body(design, manifest))
    game = code or MOCK_PLATFORMER.read_text(encoding="utf-8")
    return build_game(tmp_path / "build", title="t", controls="c", files={"game.js": game},
                      assets=manifest, images=art.files(), credits=[], levels=script)  # fmt: skip


def levels_of(records):
    return [{"n": r.spec.n, "name": r.spec.name, "events": r.spec.events, "steps": len(r.route)}
            for r in records]  # fmt: skip


def test_the_bot_clears_a_gated_level_in_the_browser(tmp_path):
    design, plan = slime_design(), slime_plan()
    records = [check(GATED, n=1, events=["crate_open", "enemy_defeat", "wall_break", "level_complete"])]
    build = built(tmp_path, design, plan, records)
    report = playtest_levels(build, [], levels_of(records))
    level = report["levels"][0]
    assert level["cleared"] and level["missing"] == [] and report["errors"] == [], level
    assert level["events"]["dash"] >= 1 and level["events"]["power"] >= 2


def test_coins_in_the_air_are_no_floor_and_a_checkpoint_needs_no_art(tmp_path):
    """Live 2026-11-02: a jump through a floating coin read as a landing (Arcade sets
    body.touching for overlaps too) and the bot steered back into the pit; the design had no
    checkpoint entity, so every checkpoint was dropped."""
    design = slime_design()
    design.entities = [e for e in design.entities if e.id != "flag"]
    plan = slime_plan()
    rows = [" " * 40] * 5 + [" " * 11 + "ooo" + " " * 26, " " * 40,
                             "@  c      F         c          F      D  ",
                             "#" * 11 + "   " + "#" * 26, "#" * 11 + "   " + "#" * 26]  # fmt: skip
    rec = evaluate(spec(rows, n=1, challenges=[ChallengeAt(name="Small Gap", column=11)],
                        events=["crate_open"]), 1, design, plan, None, player_body(design))  # fmt: skip
    assert rec.solved and any("SDK draws a flag" in n for n in rec.notes)
    assert not any("cannot be reached" in p for p in rec.problems)  # coins in the jump's arc
    assert next(e for e in rec.spec.legend if e.kind == "checkpoint").entity == ""
    build = built(tmp_path, design, plan, [rec])
    report = playtest_levels(build, [], levels_of([rec]))
    level = report["levels"][0]
    assert level["cleared"] and report["errors"] == [], level
    assert level["events"]["checkpoint"] >= 1 and level["events"].get("respawn", 0) == 0


def test_a_mechanic_that_does_not_work_gets_the_bot_stuck(tmp_path):
    design, plan = slime_design(), slime_plan()
    records = [check(GATED, n=1)]
    broken = MOCK_PLATFORMER.read_text(encoding="utf-8").replace(
        "t.kind === 'crate' && this.can(t, how)", "false")
    build = built(tmp_path, design, plan, records, broken)
    report = playtest_levels(build, [], levels_of(records))
    level = report["levels"][0]
    assert not report["passed"] and level["ended"] == "stuck"
    assert "did not open when touched" in level["bot"]["detail"]


# --- the gated run: levels after G2, per-level G4, the bundle -------------------------------------


def answer(store, ident, **kwargs):
    ScriptedHuman([Feedback(action="approve", **kwargs)]).answer(Gates(store), ident)
    workflow_step(store, ident)


def submit(store, ident, text="", form=None):
    ScriptedHuman([Feedback(action="submit", text=text, form=form or {})]).answer(Gates(store), ident)
    workflow_step(store, ident)


def read(store, ident, name):
    return json.loads(next(a for a in store.artifacts(ident) if a.name == name).data)


def test_levels_are_written_built_played_and_revised_one_at_a_time(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.workspace = tmp_path
    run = start_run(store, cfg, mock=True, goal=WORDS)
    ident = run.id
    workflow_step(store, ident)
    answer(store, ident, form={"genre": "genres/side-scrolling-platformer"})
    answer(store, ident, form={"perspective": "side-scroll"})
    answer(store, ident)
    submit(store, ident)  # approve the design: levels, art, code, per-level playtests
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and "3/3 levels cleared by the bot" in gate.title
    assert gate.form_schema["properties"]["level"]["enum"][1:] == ["Level 1: Level 1", "Level 2: Level 2",
                                                                   "Level 3: Level 3"]  # fmt: skip
    names = {a.id: a.name for a in store.artifacts(ident)}
    shown = [names[i] for i in gate.artifacts_to_show]
    assert [n for n in shown if "/levels/" in n] == [f"game/v1/levels/level-{n}.png" for n in (1, 2, 3)]
    report = read(store, ident, "game/v1/report.json")
    assert report["passed"] and report["levels_version"] == 1
    assert [lv["play"] for lv in report["levels"]] == [f"{report['play']}?level={n}" for n in (1, 2, 3)]
    assert all(lv["cleared"] and lv["solved"] and not lv["problems"] for lv in report["levels"])
    build = tmp_path / "runs" / ident / "game" / "v1" / "build"
    assert {"levels.js", "sflevel.js", "levels.json"} <= {p.name for p in build.iterdir()}
    db = open_db(tmp_path / "runs" / ident / "game" / "db", store, ident)
    entries = {e.id: e for e in db.scan()[0]}
    assert {"levels/1", "levels/2", "levels/3"} <= set(entries)
    assert entries["levels/2"].metadata.resource == "/references/levels/2.json"
    assert (db.root / "references" / "levels" / "2.png").exists() and "# Schema" in entries["levels/2"].body
    assert validate(db, external=False).errors == [] and conformance(db) == []

    # Feedback on level 2 only: a new level set with level 2 revised; the same code; all replayed.
    before = {n: read(store, ident, f"game/levels/v1/level-{n}.json")["spec"]["rows"] for n in (1, 2, 3)}
    submit(store, ident, "Make it longer", form={"level": "Level 2: Level 2"})
    after = {n: read(store, ident, f"game/levels/v2/level-{n}.json")["spec"]["rows"] for n in (1, 2, 3)}
    assert [n for n in (1, 2, 3) if before[n] != after[n]] == [2]
    v2 = read(store, ident, "game/v2/report.json")
    assert v2["levels_version"] == 2 and all(lv["cleared"] for lv in v2["levels"])
    assert read(store, ident, "game/v2/code.json") == read(store, ident, "game/v1/code.json")
    assert read(store, ident, "game/levels/v2/levels.json")["changed"] == [2]
