"""Each agent's report, in simple words and point by point.

Entry (at G2): the Game Designer writes the team's task list. Every agent that will work on
the build gets a plan: what must be ready before it starts (its entry gate), the steps it will
take for this game, and how we know it is done (its exit gate). The human approves the plans
with the design.

Exit (after each build, before G3/G4): every agent that worked says what it did, and code
checks its exit gate from the build report (the model never marks its own work as done).

Muse Spark writes the task list; code keeps it to the agents that really work on this build,
in their working order, fills what is missing from a plan made from the design, and keeps each
point short.
"""

from collections import Counter

from pydantic import Field

from spriteforge.agents.models import Contract

from .design import GameDesign
from .plan import PlanRecord

POINT = 140  # the longest point, in characters
MAX_TASKS = 8

# What each agent really does in a build: the task list may only promise these.
JOBS = {
    "Level/Tile Designer": "lays out each level as tiles from the plan, checks that every jump "
    "can be made, draws a preview of each level",
    "Art Director": "plans the art of every thing in the design (look, size, animation rows), "
    "keeps one palette and style, makes the art sheet",
    "Pixel Artist": "takes art from the library first, asks Muse Image for the rest, cleans "
    "each image to the palette and size, draws the animation frames",
    "Menu Designer": "writes every menu screen (title, pause, game over ...), the theme colours "
    "and the title art, takes a screenshot of each screen",
    "Sound Director": "picks a sound for every moment of the game (jump, hit, pick up, win ...)",
    "Programmer": "writes the game's code (Phaser) from the design and the art, then fixes it "
    "until the design's checks pass",
    "QA Playtester": "lets a bot play the game, checks every design check, writes its own code "
    "tests, records a video of the autoplay",
    "Art Reviewer": "looks at the art inside the running game and fixes art that is hard to "
    "read before you see it",
}


class AgentPlan(Contract):
    agent: str = Field(description="the agent's name, exactly as listed")
    starts_when: list[str] = Field(description="1-3 points: what must be ready before it starts")
    tasks: list[str] = Field(description="3-8 steps it will do for THIS game, in order")
    done_when: list[str] = Field(description="1-3 points: how we know it is finished")


class TaskList(Contract):
    agents: list[AgentPlan]


class AgentReport(Contract):
    agent: str
    did: list[str]
    checks: list[tuple[str, bool]]  # an exit gate point and whether the build meets it


def clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= POINT else text[: POINT - 1].rstrip() + "…"


def names(items, n: int = 4) -> str:
    items = [str(i) for i in items if str(i).strip()]
    more = len(items) - n
    return ", ".join(items[:n]) + (f" and {more} more" if more > 0 else "")


def build_agents(design: GameDesign, plan: PlanRecord | None, levels: bool, menus: bool,
                 sounds: bool, review: bool) -> list[str]:  # fmt: skip
    """The agents that work on a build, in their working order."""
    on = {"Level/Tile Designer": levels, "Menu Designer": menus, "Sound Director": sounds,
          "Art Reviewer": review}  # fmt: skip
    return [a for a in JOBS if on.get(a, True)]


# --- entry: the task list -----------------------------------------------------------------------


def default_tasks(design: GameDesign, plan: PlanRecord | None, agents: list[str]) -> TaskList:
    """A plain plan made from the design (offline runs, and whatever the model left out)."""
    things = names(e.id.replace("_", " ") for e in design.entities)
    player = next((e for e in design.entities if e.role == "player"), None)
    moving = [e for e in design.entities if e.animations]
    levels = plan.plan.levels if plan else []
    k = plan.kinematics if plan else None
    approved = "You approve the game design and this plan (G2)"
    plans = {
        "Level/Tile Designer": AgentPlan(
            agent="Level/Tile Designer",
            starts_when=[approved, "The game plan lists the levels and the jump numbers"],
            tasks=[
                f"Lay out {len(levels)} levels: {names(lv.name for lv in levels)}",
                *(f"Level {lv.n} teaches: {lv.teaching_goal}" for lv in levels[:3]),
                (f"Keep every gap under {k.max_gap} tiles and every ledge under {k.max_rise} "
                 "tiles so each jump can be made") if k else "Check that every jump can be made",
                "Draw a preview picture of each level",
            ],
            done_when=["Every level can be finished", "The bot clears every level"],
        ),
        "Art Director": AgentPlan(
            agent="Art Director",
            starts_when=[approved],
            tasks=[
                f"Plan the art for {len(design.entities)} things: {things}",
                f"Keep one look for all of it: {design.look}",
                f"Plan the animations of {len(moving)} moving things" if moving
                else "Keep every thing still (no animations in the design)",
                "Make one sheet that shows all the art together",
            ],
            done_when=["Every thing in the design has an art plan", "The art sheet is ready"],
        ),
        "Pixel Artist": AgentPlan(
            agent="Pixel Artist",
            starts_when=["The Art Director's art plan is ready"],
            tasks=[
                "Take art from the art library first",
                "Ask Muse Image to draw the things the library does not have",
                *(f"Draw the {e.id.replace('_', ' ')}: {e.look}" for e in design.entities[:4]),
                "Clean every image to the game's palette and size",
            ],
            done_when=["Every thing has its art", "No art is missing from the game"],
        ),
        "Menu Designer": AgentPlan(
            agent="Menu Designer",
            starts_when=[approved, "The art is drawn (the menus use its colours)"],
            tasks=[
                "Write the title, pause and game over screens",
                f"Make the title picture for '{design.title}' with Muse Image",
                "Take theme colours from the game's art",
                "Take a screenshot of every screen for you to check",
            ],
            done_when=["Every menu screen opens", "The title art is ready"],
        ),
        "Sound Director": AgentPlan(
            agent="Sound Director",
            starts_when=[approved],
            tasks=[
                "List every moment that needs a sound",
                *[f"When the player sees '{f.event}', play: {f.hear}"
                  for f in (plan.plan.feel if plan else []) if f.hear.strip()][:4],
                "Pick a sound for each moment",
            ],
            done_when=["Every moment has a sound", "The bot hears each sound in play"],
        ),
        "Programmer": AgentPlan(
            agent="Programmer",
            starts_when=[approved, "The art is ready"],
            tasks=[
                f"Write the game '{design.title}' in Phaser",
                f"Controls: {design.controls}",
                *(f"Rule: {r}" for r in design.rules[:3]),
                f"Show on screen: {names(design.hud, 3)}" if design.hud else "Keep the screen clean",
                f"How you win: {design.win}",
                "Fix the code until every check passes",
            ],
            done_when=["The game starts with no errors",
                       f"All {len(design.checks)} checks of the design pass"],
        ),
        "QA Playtester": AgentPlan(
            agent="QA Playtester",
            starts_when=["The Programmer's game runs"],
            tasks=[
                "Let a bot play the game",
                *(f"Check: {c.description}" for c in design.checks[:3]),
                "Write my own tests and play them with real key presses",
                "Record a video of the autoplay for you",
            ],
            done_when=["Every check is marked pass or fail", "A video or picture of play is ready"],
        ),
        "Art Reviewer": AgentPlan(
            agent="Art Reviewer",
            starts_when=["The game runs with its art"],
            tasks=[
                "Look at the art inside the running game",
                f"Check that the {player.id.replace('_', ' ') if player else 'player'} is easy to see",
                "Fix art that is hard to read before you see it",
                "List smaller ideas for you as suggestions",
            ],
            done_when=["No big art problem is left"],
        ),
    }
    return TaskList(agents=[plans[a] for a in agents])


def tasks_prompt(design: GameDesign, plan: PlanRecord | None, agents: list[str]) -> str:
    jobs = "\n".join(f"- {a}: {JOBS[a]}" for a in agents)
    return (
        "Write the team's task list for building this game. One entry per agent below, in this "
        "order. Write in simple, short words that someone who is not a programmer understands; "
        "each point is one short sentence (under 20 words).\n"
        "For each agent: starts_when (1-3 points: what must be ready before it starts), tasks "
        "(3-8 steps it will do for THIS game, in order, naming the real things of the design: "
        "its characters, levels, rules and checks), done_when (1-3 points: how we know it is "
        "finished). Only promise what the agent's job below covers.\n"
        f"Agents and their jobs:\n{jobs}\n"
        f"The design:\n{design.model_dump_json()}\n"
        + (f"The game plan:\n{plan.plan.model_dump_json()}\n" if plan else "")
    )


def fit_tasks(answer: TaskList, fallback: TaskList) -> TaskList:
    """Only the agents of this build, in working order; what the model left empty comes from
    the plain plan; every point short."""
    said = {p.agent.strip().lower(): p for p in answer.agents}

    def pick(got: AgentPlan | None, base: AgentPlan, field: str, most: int) -> list[str]:
        points = [clip(p) for p in getattr(got, field) if str(p).strip()] if got else []
        return (points or [clip(p) for p in getattr(base, field)])[:most]

    out = []
    for base in fallback.agents:
        got = said.get(base.agent.lower())
        out.append(AgentPlan(agent=base.agent, starts_when=pick(got, base, "starts_when", 3),
                             tasks=pick(got, base, "tasks", MAX_TASKS),
                             done_when=pick(got, base, "done_when", 3)))
    return TaskList(agents=out)


def entry_sections(p: AgentPlan, tools: list[str] = ()) -> list[dict]:
    """`tools`: the MCP tools and skills the agent can use to look things up."""
    return [
        {"title": "Starts when (entry gate)", "points": p.starts_when},
        {"title": "What I will do", "points": p.tasks, "numbered": True},
        {"title": "Done when (exit gate)", "points": p.done_when},
        *([{"title": "Tools and skills I can use", "points": list(tools)}] if tools else []),
    ]


def tasks_markdown(tasks: TaskList, version: int, title: str) -> str:
    out = [f"# Team plan for design v{version}: {title}",
           "Each agent's plan. Approve the design (G2) to let them start."]  # fmt: skip
    for p in tasks.agents:
        out.append(f"\n## {p.agent}")
        out += sections_markdown(entry_sections(p))
    return "\n".join(out) + "\n"


def sections_markdown(sections: list[dict]) -> list[str]:
    out = []
    for s in sections:
        out.append(f"\n**{s['title']}**")
        out += [f"{i + 1}. {p}" if s.get("numbered") else f"- {p}"
                for i, p in enumerate(s["points"])]  # fmt: skip
    return out


def designer_report(design: GameDesign, plan: PlanRecord | None, research: bool,
                    tasks: TaskList, version: int) -> AgentReport:  # fmt: skip
    """The Game Designer's own exit report at G2: what it wrote."""
    did = []
    if research:
        did.append("Studied similar games on the web")
    if plan:
        did.append(f"Wrote the game plan with {len(plan.plan.requirements)} of your wishes "
                   f"and {len(plan.plan.levels)} levels")  # fmt: skip
    did += [
        f"Wrote design v{version}: '{design.title}'",
        f"Listed {len(design.entities)} things: {names(e.id.replace('_', ' ') for e in design.entities)}",
        f"Wrote {len(design.rules)} rules and {len(design.checks)} checks the game must pass",
        f"Made a plan for {len(tasks.agents)} agents: {names([p.agent for p in tasks.agents], 8)}",
    ]
    checks = [("Every part of the design is filled in", bool(design.entities and design.checks))]
    if plan:
        checks.append(("The plan has no problem code could not fix", not plan.problems))
    return AgentReport(agent="Game Designer", did=[clip(d) for d in did], checks=checks)


# --- exit: what each agent did --------------------------------------------------------------------


def art_counts(art: list[dict]) -> str:
    def kind(source: str) -> str:
        s = source.lower()
        if "review" in s:
            return "redrawn by the Art Reviewer"
        if "reuse" in s or "kept" in s or "locked" in s:
            return "kept from the last build"
        if "librar" in s:
            return "from the library"
        if "muse" in s:
            return "drawn by Muse Image"
        return "drawn by code"

    counts = Counter(kind(a.get("source", "")) for a in art)
    return ", ".join(f"{n} {k}" for k, n in counts.most_common())


def exit_reports(report: dict, design: GameDesign) -> list[AgentReport]:
    """What each agent of the build did, and its exit gate checked by code from report.json."""
    art = report.get("art", [])
    checks = report.get("checks", [])
    ok = sum(1 for c in checks if c.get("ok"))
    out = []
    if levels := report.get("levels"):
        cleared = sum(1 for lv in levels if lv.get("cleared"))
        out.append(AgentReport(agent="Level/Tile Designer", did=[
            f"Laid out {len(levels)} levels: {names(lv['name'] for lv in levels)}",
            (f"Code checked the jumps: {sum(1 for lv in levels if lv.get('solved'))} of "
             f"{len(levels)} levels can be finished"),
            "Drew a preview of each level",
        ], checks=[("Every level can be finished", all(lv.get("solved") for lv in levels)),
                   (f"The bot clears every level ({cleared}/{len(levels)})", cleared == len(levels))]))  # fmt: skip
    motion = report.get("motion") or {}
    out.append(AgentReport(agent="Art Director", did=[
        f"Planned the art for {len(art)} things",
        *([f"Planned {len(motion['rows'])} animation rows"] if motion.get("rows") else []),
        "Made the art sheet that shows all the art together",
    ], checks=[("Every thing in the design has art",
                {a["entity"] for a in art} >= {e.id for e in design.entities})]))  # fmt: skip
    warnings = report.get("warnings", [])
    out.append(AgentReport(agent="Pixel Artist", did=[
        f"Made art for {len(art)} things: {art_counts(art)}" if art else "No art was needed",
        "Cleaned every image to the game's palette and size",
        *([f"{len(warnings)} warning(s) about the art (see the report)"] if warnings else []),
    ], checks=[("No art is missing", bool(art)), ("No art warnings", not warnings)]))
    if menus := report.get("menus"):
        title = menus.get("title_art") or {}
        out.append(AgentReport(agent="Menu Designer", did=[
            f"Made {len(menus.get('screens', []))} menu screens",
            f"Made the title art ({title.get('source', 'none')})",
            f"Took the theme colours from the art ({(menus.get('theme') or {}).get('style', 'plain')} style)",
        ], checks=[("Every menu screen opens", not menus.get("errors")),
                   ("No menu problem left", not menus.get("problems"))]))  # fmt: skip
    if sounds := report.get("sounds"):
        cues, missed = sounds.get("cues", []), sounds.get("never_heard", [])
        out.append(AgentReport(agent="Sound Director", did=[
            f"Picked {len(cues)} sounds: {names(c['name'] for c in cues)}",
            f"The bot heard {len(cues) - len(missed)} of them while playing",
        ], checks=[("Every moment has a sound", bool(cues)),
                   ("The bot heard every sound", not missed)]))  # fmt: skip
    attempts = report.get("attempts", [])
    out.append(AgentReport(agent="Programmer", did=[
        f"Wrote the game '{report.get('title', design.title)}'",
        f"Ran it {len(attempts)} time(s) and fixed it after each failed try" if len(attempts) > 1
        else "It ran on the first try",
        f"{ok} of {len(checks)} checks of the design pass",
        *([f"Still open: {clip(p)}" for p in report.get("problems", [])[:2]]),
    ], checks=[("The game starts with no errors", bool(report.get("play"))),
               (f"All checks of the design pass ({ok}/{len(checks)})", bool(report.get("passed")))]))  # fmt: skip
    qa = report.get("qa") or {}
    out.append(AgentReport(agent="QA Playtester", did=[
        "Let a bot play the game",
        f"Checked {len(checks)} checks: {ok} pass, {len(checks) - ok} fail",
        *([f"Ran {qa['passed'] + qa['failed']} of my own tests: {qa['passed']} pass"] if qa else []),
        "Recorded the autoplay for you",
    ], checks=[("Every check is marked pass or fail", bool(checks)),
               *([("All my tests pass", not qa.get("failed"))] if qa else [])]))  # fmt: skip
    if review := report.get("art_review"):
        issues = review.get("issues", [])
        fixed = sum(1 for i in issues if i.get("status") == "fixed")
        blocking = sum(1 for i in issues if i.get("severity") == "blocking" and i.get("status") != "fixed")
        out.append(AgentReport(agent="Art Reviewer", did=[
            "Looked at the art inside the running game",
            f"Found {len(issues)} issue(s) and fixed {fixed}",
            *([f"{len(issues) - fixed} smaller idea(s) are suggestions for you"] if len(issues) > fixed else []),
        ], checks=[(f"No big art problem left ({blocking} left)", not blocking)]))  # fmt: skip
    return [r.model_copy(update={"did": [clip(d) for d in r.did]}) for r in out]


def looked_up(tools: list[str]) -> str:
    counts = Counter(tools)
    return clip(f"Looked things up {len(tools)} time(s) with my tools: "
                + ", ".join(f"{t} ×{n}" if n > 1 else t for t, n in counts.most_common()))


def exit_sections(r: AgentReport) -> list[dict]:
    return [
        {"title": "What I did", "points": r.did},
        {"title": "Exit gate (checked by code)",
         "points": [("✅ " if met else "⚠️ ") + text for text, met in r.checks]},
    ]


def team_markdown(reports: list[AgentReport], version: int, title: str) -> str:
    out = [f"# Team report for build v{version}: {title}", "What each agent did, point by point."]
    for r in reports:
        out.append(f"\n## {r.agent}")
        out += sections_markdown(exit_sections(r))
    return "\n".join(out) + "\n"


def payload(stage: str, title: str, sections: list[dict], key: str) -> dict:
    """The `agent.report` event an agent's chat shows as a report card."""
    return {"stage": stage, "title": title, "sections": sections, "key": key}
