"""The game's structure (L2.5): its levels in order, what carries over between them, what is
saved and how the game is won, derived by code from the design and the game plan (no model
call). G2 shows it beside the design; feedback that changes the plan's levels changes it.

A game whose levels are data (a side-view game with a plan, L2.3) has the plan's levels; any
other game is one level: its whole session. The SDK's flow (`sfflow.js`) plays the structure:
title, level intro, results between levels, game over, victory, progress saved in the browser.
"""

import re

from pydantic import Field, model_validator

from spriteforge.agents.models import Contract

from .design import GameDesign
from .plan import PlanRecord

MAX_LEVELS = 8
SCORE_WORDS = re.compile(r"\b(score|points?|gems?|coins?|kills?|stars?)\b", re.IGNORECASE)


class PlannedLevel(Contract):
    n: int
    name: str = Field(min_length=1, max_length=40)
    goal: str = Field(description="what the level is about, in one line")


class GameStructure(Contract):
    levels: list[PlannedLevel]
    carries: list[str] = Field(description="what carries over from one level to the next")
    saved: list[str] = Field(description="what is saved between sessions (in the browser)")
    win: str = Field(description="how the game is won")
    lose: str = Field(description="how a level is lost ('' when it cannot be)")

    @model_validator(mode="after")
    def check(self):
        if not 1 <= len(self.levels) <= MAX_LEVELS:
            raise ValueError(f"a game has 1 to {MAX_LEVELS} levels, not {len(self.levels)}")
        names = [lv.name.casefold() for lv in self.levels]
        if len(set(names)) != len(names):
            raise ValueError("level names must be unique")
        if [lv.n for lv in self.levels] != list(range(1, len(self.levels) + 1)):
            raise ValueError("levels are numbered 1, 2, 3 ... in order")
        return self


def has_score(design: GameDesign) -> bool:
    text = " ".join([*design.hud, *(c.event for c in design.checks)])
    return bool(SCORE_WORDS.search(text.replace("_", " ")))


def unique_names(names: list[str]) -> list[str]:
    out, seen = [], set()
    for name in names:
        base, n = (name.strip() or "Level")[:36], 2
        name = base
        while name.casefold() in seen:
            name, n = f"{base} {n}", n + 1
        seen.add(name.casefold())
        out.append(name)
    return out


def make_structure(design: GameDesign, plan: PlanRecord | None, levels: bool) -> GameStructure:
    """`levels`: the game plays its plan's levels as data (`pipeline.uses_levels`)."""
    planned = plan.plan.levels[:MAX_LEVELS] if (levels and plan) else []
    if planned:
        names = unique_names([lv.name for lv in planned])
        rows = [PlannedLevel(n=i, name=name, goal=(lv.teaching_goal or lv.new_idea)[:200])
                for i, (lv, name) in enumerate(zip(planned, names, strict=True), 1)]  # fmt: skip
    else:
        rows = [PlannedLevel(n=1, name=design.title[:40] or "Level 1", goal=design.win[:200])]
    carries = []
    if len(rows) > 1:
        held = [a.name for a in plan.plan.abilities if a.unlocked_by.strip().lower() == "start"] if plan else []
        carries.append("the abilities held from the start" + (f" ({', '.join(held)})" if held else ""))
        if has_score(design):
            carries.append("the score")
    saved = ["the settings (difficulty, fewer flashes)", "the best result of each level"]
    if len(rows) > 1:
        saved.insert(0, "the levels unlocked so far (Continue and Level select)")
    win = (f"Finish level {len(rows)} ({rows[-1].name})" if len(rows) > 1 else design.win) or "Win"
    return GameStructure(levels=rows, carries=carries, saved=saved, win=win, lose=design.lose)


def structure_markdown(s: GameStructure) -> str:
    rows = "\n".join(f"| {lv.n} | {lv.name} | {lv.goal} |" for lv in s.levels)
    bullets = lambda items: "\n".join(f"- {i}" for i in items) or "- (nothing)"
    return (
        "# Game structure\n\nThe SDK plays it: a title, the levels in order (an intro before each, "
        "results after each), pause on Escape, game over and victory screens. Progress is saved "
        "in the browser.\n\n"
        f"## Levels\n| n | name | goal |\n|---|---|---|\n{rows}\n\n"
        f"## Carries over between levels\n{bullets(s.carries)}\n\n"
        f"## Saved\n{bullets(s.saved)}\n\n"
        f"**Win:** {s.win}  \n**Lose:** {s.lose or '(cannot be lost: the player is sent back to a checkpoint)'}\n"
    )
