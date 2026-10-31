"""Reviewer feedback on a playable build -> what to redo (Director, Muse Spark).

Only the parts the feedback is about are redone: the design (a new design version), the art
of named entities (Muse edits of the previous image; everything else stays locked), and the
code (the Programmer edits the working game). Untouched parts are reused as they are.
"""

import re

from pydantic import Field

from spriteforge.agents.models import Contract

from .design import GameDesign


class ArtChange(Contract):
    entity: str = Field(description="entity id whose look should change")
    prompt: str = Field(description="the new look for Muse Image: subject, colours, materials")


class Revision(Contract):
    design_changes: str = Field(
        description="rule, entity, progression or difficulty changes for the design doc; ''"
    )
    art_changes: list[ArtChange] = Field(description="entities to redraw; [] if none")
    code_instructions: str = Field(
        description="behaviour, controls, feel or UI changes for the programmer; ''"
    )


def revision_prompt(design: GameDesign, art: dict[str, str], feedback: str) -> str:
    sources = "\n".join(f"* {eid}: {src}" for eid, src in art.items())
    return (
        "A human played this build and wrote the feedback below. Split it into what must be "
        "redone: design_changes (new or changed rules, entities, upgrades, difficulty), "
        "art_changes (only entities whose look they want different, with the new look), and "
        "code_instructions (how the game should behave or feel). Leave a part empty when the "
        "feedback does not touch it; do not invent changes.\n\n"
        f"Feedback:\n{feedback.strip()}\n\nDesign:\n{design.model_dump_json()}\n\n"
        f"Current art sources:\n{sources}"
    )


def fallback_revision(design: GameDesign, feedback: str) -> Revision:
    """Offline Director: entities named in the feedback are redrawn with it appended to their
    look; everything else in the feedback goes to the programmer."""
    text = feedback.lower()
    changes = [
        ArtChange(entity=e.id, prompt=f"{e.look}; {feedback.strip()}")
        for e in design.entities
        if re.search(rf"\b{re.escape(e.id.replace('_', ' '))}s?\b", text.replace("_", " "))
    ]
    return Revision(
        design_changes="",
        art_changes=changes,
        code_instructions="" if changes else feedback.strip(),
    )
