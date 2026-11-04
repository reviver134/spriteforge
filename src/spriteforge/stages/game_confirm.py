"""The Director's plan for G4/G5 feedback, approved by the human before any agent acts on it.

At G4 (playtest) or G5 (final review) the Director splits the human's words into design, art,
code, level, menu, sound, story and QA changes (a `Revision`). Nothing is built, drawn or
written from it until the human approves that reading at a confirm gate:

* empty feedback approves the plan: the round goes on (a build, or only a story or sounds);
* text corrects it: the round starts again under a new key (gate-5.1, as a Go back does) and
  the Director routes the original words with every correction; another confirm gate follows.

The approval belongs to one plan (`game/feedback/gate-{n}-plan.md`): a Go back after it makes a
new plan, which is confirmed again. Rounds whose plan no model interpreted (an art-review
suggestion applied, feedback on one chosen level, changes from the agent chats the card
listed) are the human's own instructions and need no confirmation.
"""

from spriteforge.core.gates import Gate, Gates, new_gate
from spriteforge.core.state import Store
from spriteforge.game.revision import Revision

STAGE = "confirm"


def plan_name(n: str) -> str:
    return f"game/feedback/gate-{n}-plan.md"


def resolve(history: list[Gate]) -> tuple[Gate, int, Gate | None]:
    """The feedback gate whose round of work this is, its number (1-based), and the confirm
    gate answered last (None when the last gate is not a confirm gate)."""
    last = history[-1]
    if last.stage != STAGE:
        return last, len(history), None
    i = next(i for i, g in enumerate(history) if g.id == last.parent_id)
    return history[i], i + 1, last


def corrections(history: list[Gate], parent: Gate) -> list[str]:
    """What the human said the Director got wrong, oldest first."""
    return [g.response.text.strip() for g in history
            if g.stage == STAGE and g.parent_id == parent.id and g.status == "changes_requested"
            and g.response and g.response.text.strip()]  # fmt: skip


def replan(store: Store, run_id: str, confirm: Gate, parent: Gate, old: str) -> None:
    """A correction: start round `old` again under a new key, with the chat changes it took
    queued again (once per confirm gate; a re-entered step does nothing)."""
    events = store.events(run_id)
    if any(e.type == "round.replanned" and e.payload.get("confirm") == confirm.id for e in events):
        return
    taken = [i for e in events if e.type == "chat.applied" and e.payload.get("where") == f"gate-{old}"
             for i in e.payload.get("ids", [])]  # fmt: skip
    if taken:
        store.append_event(run_id, "Director", "chat.unapplied", {"ids": taken, "where": old})
    store.append_event(run_id, "Director", "round.replanned",
                       {"gate": parent.id, "confirm": confirm.id, "from": old})  # fmt: skip


def confirmed(ctx, n: str) -> bool:
    """The human approved round n's plan."""
    plan = ctx.artifacts.get(plan_name(n))
    return plan is not None and any(
        g.stage == STAGE and g.status == "approved" and plan.id in g.artifacts_to_show
        for g in Gates(ctx.store).all(ctx.run_id)
    )


def plan_markdown(revision: Revision, feedback: str) -> str:
    quoted = "\n".join(f"> {line}" for line in feedback.strip().splitlines()) or "> (no text)"
    rows = []
    if revision.design_changes.strip():
        rows.append(f"**Design** (Game Designer, a new design version): {revision.design_changes.strip()}")
    for a in revision.art_changes:
        rows.append(f"**Art** (Pixel Artist, Muse Image): redraw `{a.entity}` as: {a.prompt}")
    if revision.code_instructions.strip():
        rows.append(f"**Code** (Programmer): {revision.code_instructions.strip()}")
    for lv in revision.level_changes:
        rows.append(f"**Level {lv.level}** (Level/Tile Designer): {lv.change}")
    if revision.menu_changes.strip():
        rows.append(f"**Menus** (Menu Designer): {revision.menu_changes.strip()}")
    if revision.sound_changes.strip():
        rows.append(f"**Sounds** (Sound Director): {revision.sound_changes.strip()}")
    if revision.story_changes.strip():
        rows.append(f"**Story** (Game Designer): {revision.story_changes.strip()}")
    if revision.drop_qa_tests:
        rows.append("**QA tests dropped** (QA Playtester): " + ", ".join(revision.drop_qa_tests))
    for check in revision.add_qa_checks:
        rows.append(f"**QA check added** (QA Playtester, every build from now on): {check}")
    body = "\n".join(f"- {r}" for r in rows) or (
        "- Nothing specific was found to change. Approving builds the game again as it is; "
        "type what you want changed instead."
    )
    return (
        "# The Director's plan\n\nYour feedback:\n\n" + quoted + "\n\n## What the agents will do\n\n"
        + body + "\n\n## What stays as it is\n\nEverything not listed: accepted art stays locked, "
        "and the code is edited, not rewritten. Nothing has been built yet.\n"
    )


def open_confirm_gate(ctx, parent: Gate, n: str, revision: Revision, feedback: str) -> None:
    name = plan_name(n)
    if name not in ctx.artifacts:
        ctx.save(name, plan_markdown(revision, feedback).encode(), "Director", "text/markdown")
    g = "G5" if parent.stage == "final" else "G4"
    title = (
        f"{g} Confirm the plan: this is how the Director read your feedback, and nothing is built "
        "until you approve it. Leave feedback empty and submit to approve, or type what the "
        "Director got wrong to get a new plan"
    )
    Gates(ctx.store).open(ctx.run_id, new_gate(
        STAGE, title, [ctx.artifacts[name].id], parent_id=parent.id,
        empty_feedback_approves=True, agents=["Director"]))  # fmt: skip
