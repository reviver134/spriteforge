"""Levers and gates: a building block the Level Designer may use (the harness never asks for
one). Code's solver and the SDK's bot pull a lever and come back through the gate it opened;
a long level loses what nothing can get past instead of being replaced by code's level."""

from test_levels import LEGEND, L, built, levels_of, slime_design, slime_plan, spec

from spriteforge.game.level_gen import fallback_level
from spriteforge.game.levels import evaluate, player_body, target_cols
from spriteforge.game.pipeline import finishable
from spriteforge.game.playtest import playtest_levels

WIDTH = 40


def lever_map(ledge: int = 5) -> list[str]:
    """A lever on a ledge (one-way tiles in row `ledge`; 5 is 3 rows above the floor, a jump
    reaches 4); a gate from the top of the level to the floor blocks the way."""
    row = lambda **at: "".join(at.get(f"c{c}", " ") for c in range(WIDTH))
    rows = []
    for r in range(8):
        at = {"c25": "G"}
        if r == ledge - 1:
            at["c10"] = "L"
        if r == ledge:
            at |= {f"c{c}": "=" for c in range(8, 13)}
        rows.append(row(**at))
    rows[7] = row(c1="@", c25="G", c37="D")
    return rows + ["#" * WIDTH, "#" * WIDTH]


def behind_its_gate() -> list[str]:
    """The lever on the floor past the gate it opens: it can never be pulled."""
    rows = [r.replace("L", " ") for r in lever_map()]
    rows[7] = rows[7][:30] + "L" + rows[7][31:]
    return rows


LEVER_LEGEND = [*LEGEND, L("L", "lever", "", gives="lever A"), L("G", "gate", "", needs="lever A")]


def lever_level(rows, legend=LEVER_LEGEND):
    design, plan = slime_design(), slime_plan()
    return evaluate(spec(rows, n=1, legend=legend, challenges=[], events=["level_complete"]),
                    1, design, plan, None, player_body(design))  # fmt: skip


def test_a_lever_on_an_upper_floor_opens_its_gate():
    rec = lever_level(lever_map())
    assert rec.solved, rec.problems
    steps = [(s["do"], s.get("thing", ""), (s.get("cell") or [0, 0])) for s in rec.route]
    pulled = next(i for i, (do, _, cell) in enumerate(steps) if do == "collect" and cell[1] == 4)
    past_gate = next(i for i, (_, _, cell) in enumerate(steps) if cell[0] > 25)
    assert pulled < past_gate  # up to the lever, then back down and through the gate
    assert [k for _, k in rec.features].count("puzzle") == 1
    assert any("the SDK draws a lever" in n for n in rec.notes)


def test_a_gate_whose_lever_cannot_be_reached_says_so():
    rec = lever_level(behind_its_gate())
    assert not rec.solved
    assert any("stays shut" in p and "lever at column 30" in p for p in rec.problems), rec.problems


def test_gates_are_linked_to_their_lever_or_left_out():
    misnamed = [*LEGEND, L("L", "lever", "", gives="lever A"), L("G", "gate", "", needs="the lever")]
    rec = lever_level(lever_map(), misnamed)
    assert rec.solved and next(e for e in rec.spec.legend if e.kind == "gate").needs == "lever A"
    no_lever = [*LEGEND, L("G", "gate", "", needs="lever A")]
    rec = lever_level([r.replace("L", " ") for r in lever_map()], no_lever)
    assert not any(e.kind == "gate" for e in rec.spec.legend)
    assert any("no lever opens the gate" in n for n in rec.notes)


def test_a_level_that_cannot_be_finished_is_repaired_before_it_is_replaced():
    """Live 2026-11-04: a 150-column level was replaced by code's 27-column one because one
    thing nothing could get past blocked the way. Here a gate's lever is out of reach: code
    takes out the gate (and its lever) and keeps the Level Designer's level."""
    design, plan = slime_design(), slime_plan()
    rec = lever_level(behind_its_gate())
    assert not rec.solved
    fixed = finishable(rec, design, plan, [])
    assert fixed.solved and len(fixed.spec.rows[0]) == WIDTH
    assert not any(e.kind in {"gate", "lever"} for e in fixed.spec.legend)
    assert any("code removed enemies and gates" in n
               for n in fixed.notes), fixed.notes


def test_the_offline_level_is_long_and_goes_up_and_down():
    design, plan = slime_design(), slime_plan()
    for n in (1, 2):
        level = fallback_level(n, design, plan)
        assert len(level.rows[0]) >= target_cols(n) - 8
        rec = evaluate(level, n, design, plan, None, player_body(design))
        assert rec.solved, rec.problems
        tops = {next(r for r, row in enumerate(level.rows) if row[c] == "#")
                for c in range(len(level.rows[0])) if any(row[c] == "#" for row in level.rows)}
        assert len(tops) >= 3  # the ground rises to ledges and drops into dips


def test_the_bot_pulls_the_lever_in_the_browser(tmp_path):
    design, plan = slime_design(), slime_plan()
    rec = lever_level(lever_map())
    build = built(tmp_path, design, plan, [rec])
    report = playtest_levels(build, [], levels_of([rec]))
    level = report["levels"][0]
    assert level["cleared"] and report["errors"] == [], level
    assert level["events"].get("lever", 0) == 1
