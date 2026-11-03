"""Endless games (runners, endless survival) are not built as levels with exits: the level bot
walks a fixed route to an exit, which a world that scrolls by itself never offers (live
2026-11-03: a unicorn runner failed four playtests that way)."""

import pytest
from test_levels import slime_design, slime_plan

from spriteforge.game.codegen import requirements_text
from spriteforge.game.design import GameDesign, design_prompt, is_endless
from spriteforge.game.pipeline import uses_levels


def runner(**changes) -> GameDesign:
    return slime_design().model_copy(update=changes)


def test_a_planned_side_view_game_uses_levels_unless_endless():
    plan = slime_plan()
    assert uses_levels(slime_design(), plan)
    assert not uses_levels(runner(endless=True), plan)


@pytest.mark.parametrize(
    ("text", "endless"),
    [
        ("Survive the endless rush to the 5 minute cap for max distance", True),
        ("An auto-runner through a rainbow meadow", True),
        ("The unicorn auto-runs; tap to jump", True),
        ("A never-ending sprint", True),
        ("Reach the exit door of the last level", False),
        ("Run to the flag", False),
    ],
)
def test_what_the_design_says_makes_it_endless(text, endless):
    """Designs written before the flag (or with it left false) are caught by their words."""
    design = runner(win=text)
    assert not design.endless
    assert is_endless(design) is endless
    assert uses_levels(design, slime_plan()) is not endless


def test_an_auto_runner_is_endless_by_its_world_or_rules():
    """Live: 'Rainbow Rush' had a 180 s countdown as its win, but its world was an endless
    track and its first rule was auto-run."""
    assert is_endless(runner(world="Endless side-scrolling track of chunky platforms"))
    assert is_endless(runner(rules=["Auto-run forward automatically; cannot stop"]))
    assert not is_endless(runner(rules=["Enemies respawn endlessly from the pit"]))


def test_designs_saved_before_the_flag_still_load():
    saved = slime_design().model_dump()
    del saved["endless"]
    assert GameDesign.model_validate(saved).endless is False
    # The flag is required in the schema Spark answers with (strict structured output).
    assert "endless" in GameDesign.model_json_schema()["required"]


def test_prompts_ask_for_a_win_the_playtest_can_see():
    prompt = design_prompt("an endless unicorn runner")
    assert "* endless: true for a run that never ends" in prompt
    assert "'lose' for endless" not in prompt  # god mode cannot lose
    assert "This run never ends" in requirements_text(runner(endless=True))
    assert "This run never ends" not in requirements_text(slime_design())
