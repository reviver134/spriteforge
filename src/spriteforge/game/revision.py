"""Reviewer feedback on a playable build -> what to redo (Director, Muse Spark).

Only the parts the feedback is about are redone: the design (a new design version), the art
of named entities (Muse edits of the previous image; everything else stays locked), and the
code (the Programmer edits the working game). Untouched parts are reused as they are.
"""

import re

from pydantic import Field, model_validator

from spriteforge.agents.models import Contract

from .design import GameDesign


class ArtChange(Contract):
    entity: str = Field(description="entity id whose look should change")
    prompt: str = Field(description="the new look for Muse Image: subject, colours, materials")


class LevelChange(Contract):
    level: int = Field(description="the level number the feedback is about")
    change: str = Field(description="what to change in that level, in the human's words")


class Revision(Contract):
    design_changes: str = Field(
        description="rule, entity, progression or difficulty changes for the design doc; ''"
    )
    art_changes: list[ArtChange] = Field(description="entities to redraw; [] if none")
    code_instructions: str = Field(
        description="behaviour, controls, feel or UI changes for the programmer; ''"
    )
    drop_qa_tests: list[str] = Field(
        description="names of QA tests the human wants dropped ('ignore the restart test'); []"
    )
    story_changes: str = Field(
        description="only when the human asks for a story or changes to it (named characters, "
        "plot, lore): what they want; ''"
    )
    level_changes: list[LevelChange] = Field(
        description="changes to one level's layout (gaps, platforms, enemies, crates, length, "
        "difficulty of that level); [] if none"
    )

    @model_validator(mode="before")
    @classmethod
    def older(cls, data):
        """Revisions saved before QA tests, stories or levels existed lack those fields."""
        if isinstance(data, dict):
            data = {"drop_qa_tests": [], "story_changes": "", "level_changes": [], **data}
        return data

    @property
    def story_only(self) -> bool:
        """Only the story changes: nothing to rebuild."""
        return bool(self.story_changes.strip()) and not (
            self.design_changes.strip()
            or self.art_changes
            or self.code_instructions.strip()
            or self.drop_qa_tests
            or self.level_changes
        )


def revision_prompt(
    design: GameDesign, art: dict[str, str], feedback: str, qa: dict[str, bool] | None = None
) -> str:
    """`qa`: the build's QA tests by name -> passed."""
    sources = "\n".join(f"* {eid}: {src}" for eid, src in art.items())
    tests = "\n".join(
        f"* {name}: {'passed' if ok else 'FAILED'}" for name, ok in (qa or {}).items()
    )
    return (
        "A human played this build and wrote the feedback below. Split it into what must be "
        "redone: design_changes (new or changed rules, entities, upgrades, difficulty), "
        "art_changes (only entities whose look they want different, with the new look), "
        "code_instructions (how the game should behave or feel), drop_qa_tests (exact names "
        "of QA tests they want ignored or dropped, e.g. a test they judge wrong), "
        "story_changes (only when they ask for a story, named characters, a plot or lore, or "
        "for changes to the story: their words), and level_changes (feedback about one level's "
        "layout: its gaps, platforms, enemies, crates, length or difficulty, with the level "
        "number). Leave a part empty when the feedback does not touch it; do not invent "
        "changes.\n\n"
        f"Feedback:\n{feedback.strip()}\n\nDesign:\n{design.model_dump_json()}\n\n"
        f"Current art sources:\n{sources}"
        + (f"\n\nQA tests of this build:\n{tests}" if tests else "")
    )


DROP_VERBS = {"ignore", "drop", "remove", "skip", "delete", "disable"}
FILLER = {"the", "a", "an", "this", "that", "those", "these", "qa", "failing", "broken", "wrong",
          "bad", "please", "also", "too", "it", "one", "you", "can", "we", "just", "flaky"}  # fmt: skip


def dropped_tests(feedback: str, names: list[str]) -> list[str]:
    """QA tests the feedback asks to drop: "ignore the restart test" drops the tests whose name
    has every word said before "test" ("restarts after losing"). Only a part with a drop verb
    counts, or one that carries it on with "and" ("remove the idle test and the restart test");
    "the restart test is fine, drop the menu test" drops only the menu test."""
    out, carried = [], False
    for clause in re.split(r"[,;.!?]", feedback.lower()):
        parts = re.split(r"\band\b", clause)
        if not re.match(r"\s*(and|also)\b", clause):
            carried = False
        for part in parts:
            carried = carried or any(re.search(rf"\b{v}\b", part) for v in DROP_VERBS)
            match = re.search(r"([a-z0-9 -]*?)\btests?\b", part) if carried else None
            if not match:
                continue
            said = [w for w in re.findall(r"[a-z0-9]+", match.group(1))
                    if w not in FILLER | DROP_VERBS]  # fmt: skip
            for name in names:
                words = set(re.findall(r"[a-z0-9]+", name.lower()))
                stems = {w.rstrip("s") for w in words} | {w[:-2] for w in words if w.endswith("ed")}
                if said and all(w in words or w.rstrip("s") in stems for w in said):
                    out.append(name)
    return list(dict.fromkeys(out))


def fallback_revision(design: GameDesign, feedback: str, qa_names: list[str] = ()) -> Revision:
    """Offline Director: entities named in the feedback are redrawn with it appended to their
    look; QA tests it asks to ignore are dropped; a request for a story goes to the story;
    everything else goes to the programmer."""
    text = feedback.lower()
    if STORY_ASK.search(text):
        return Revision(design_changes="", art_changes=[], code_instructions="",
                        drop_qa_tests=[], story_changes=feedback.strip(), level_changes=[])  # fmt: skip
    if levels := sorted({int(n) for n in LEVEL_ASK.findall(text)}):
        return Revision(design_changes="", art_changes=[], code_instructions="", drop_qa_tests=[],
                        story_changes="", level_changes=[LevelChange(level=n, change=feedback.strip())
                                                         for n in levels])  # fmt: skip
    changes = [
        ArtChange(entity=e.id, prompt=f"{e.look}; {feedback.strip()}")
        for e in design.entities
        if re.search(rf"\b{re.escape(e.id.replace('_', ' '))}s?\b", text.replace("_", " "))
    ]
    drops = dropped_tests(feedback, list(qa_names))
    return Revision(
        design_changes="",
        art_changes=changes,
        code_instructions="" if changes or drops else feedback.strip(),
        drop_qa_tests=drops,
        story_changes="",
        level_changes=[],
    )


# "add a story", "give it a plot", "the story should ...": the offline Director's story cue.
STORY_ASK = re.compile(r"\b(stor(y|ies|yline)|plot|lore|backstor(y|ies)|narrative)\b")
# "level 2 is too long", "make level 3 harder": the offline Director's level cue.
LEVEL_ASK = re.compile(r"\blevel\s+(\d)\b")
