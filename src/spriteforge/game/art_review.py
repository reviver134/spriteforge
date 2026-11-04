"""Art Reviewer (M10.3): all of a game's art judged together and in the running game.

Inputs, built by code: the art sheet at native size and at 4x (`review_sheets`), 3 screenshots
from the playtest (early, middle, late; `playtest(capture_at=...)`), the design's entities and
the free pre-checks (`art_checks`). One Muse Spark vision call answers an `ArtReview`.

The naming test is kept blind inside that one call: sprites are numbered on the sheet, and the
entity list in the prompt is in another order and does not say which number is which. The
reviewer names every number from its pixels (`named`) and reports issues by number; code maps
numbers to entities and adds an issue when a name misses (a "gem" seen as "a blue blob"), and
a blocking one for any greybox placeholder the reviewer did not report.

Fix policy (`plan_fixes`, `fix_jobs`): the entities with a blocking issue (at most
`game.review_max_fixes` when set) are fixed before G4, as a Muse Image edit of the current image at
`game.review_image_reasoning` (high) with the M10.1 spec. Library art from an
`ai_allowed: false` pack is never sent: it is drawn fresh from text instead. Parallax
backdrops are not fixed automatically (the code uses their layer ids). Everything else stays
locked; major and minor issues are suggestions at G4. Offline, `fallback_review` builds the
review from the pre-checks alone.
"""

import io
import math
import re
from dataclasses import dataclass, field, replace
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import Field

from spriteforge.agents.models import Contract

from .actors import COLORS, canon, words
from .art import (
    PRIORITY,
    REVIEW_REASON,
    VIEWS,
    ArtPick,
    ArtPlan,
    ArtResult,
    CallSlots,
    Filled,
    Muse,
    MuseOptions,
    actor_index,
    png,
    resolve_plan,
    run_jobs,
    tile_break,
    tile_ids,
    whole_objects,
)
from .art_checks import Finding, first_frame
from .design import Entity, GameDesign
from .image_cache import ImageCache
from .tilesets import load_tilesets

Severity = Literal["blocking", "major", "minor"]
ZOOM = 4
PAD = 3  # native pixels around each sprite on the sheet
LABEL = 4  # native pixels above each sprite for its number (on the 4x sheet)
SHEET_BACKGROUND = (92, 96, 112, 255)  # neutral mid grey: neither dark nor light sprites vanish
BACKGROUND_THUMB = 80  # backgrounds are shown at most this wide (they are in the screenshots)

# Words that say nothing about what a thing is: colours, sizes, glow, generic nouns.
VAGUE = {
    "small", "tiny", "little", "big", "large", "huge", "dark", "light", "bright", "glowing",
    "glow", "pixel", "sprite", "art", "round", "shaped", "shape", "creature", "thing", "object",
    "character", "figure", "blob", "icon", "item", "some", "kind", "sort", "like", "looking",
    "simple", "cute", "colored", "coloured", "grey", "gray", "black", "white", "brown", "dot",
}  # fmt: skip


class Scores(Contract):
    consistency: int = Field(ge=1, le=5, description="one style: outline, shading, pixel size")
    readability: int = Field(ge=1, le=5, description="each thing reads at game size, in play")
    perspective: int = Field(ge=1, le=5, description="every sprite matches the camera")
    scale: int = Field(ge=1, le=5, description="sizes against each other and the hero")
    palette: int = Field(ge=1, le=5, description="one palette; threats and rewards distinct")


class Named(Contract):
    slot: int = Field(description="the number on the 4x sheet")
    seen_as: str = Field(description="what it looks like, a few words, from the pixels alone")


class ReviewIssue(Contract):
    slot: int = Field(description="the number on the 4x sheet")
    problem: str
    severity: Severity
    fix_prompt: str = Field(
        description="an instruction for an image edit of that sprite: what to change, keeping "
        "its framing, size and style"
    )


class ArtReview(Contract):
    scores: Scores
    named: list[Named]
    issues: list[ReviewIssue]


# --- inputs ------------------------------------------------------------------------------


def review_slots(art: ArtResult) -> list[str]:
    """Sheet order: the build's art order (player, enemies, pickups, tiles, ...); numbers are
    1-based positions in this list."""
    return list(art.filled)


def sheet_image(f: Filled) -> Image.Image:
    image = first_frame(f)
    if f.meta.get("role") == "background" and image.width > BACKGROUND_THUMB:
        k = math.ceil(image.width / BACKGROUND_THUMB)
        image = image.resize((image.width // k, image.height // k), Image.Resampling.BOX)
    return image


def sheet_layout(art: ArtResult, slots: list[str]) -> list[list[int]]:
    """Rows of slot indices: sprites and tiles 8 to a row, then backgrounds 2 to a row, so one
    wide background does not blow up every sprite's cell (and shrink the sprites for a vision
    model that scales the image down)."""
    background = [art.filled[eid].meta.get("role") == "background" for eid in slots]
    small = [n for n, b in enumerate(background) if not b]
    big = [n for n, b in enumerate(background) if b]
    return [small[i : i + 8] for i in range(0, len(small), 8)] + [
        big[i : i + 2] for i in range(0, len(big), 2)
    ]


def review_sheets(art: ArtResult, slots: list[str]) -> tuple[bytes, bytes]:
    """(native, 4x) sheets: every entity's first frame side by side at game size (feet on the
    cell floor), on a neutral grey; the 4x sheet numbers them. No names: the reviewer must say
    what it sees."""
    images = [sheet_image(art.filled[eid]) for eid in slots]
    placed, width, top = [], 1, 0  # (slot index, x, top, cell width, cell height)
    for row in sheet_layout(art, slots):
        cell_w = max(images[n].width for n in row) + 2 * PAD
        cell_h = max(images[n].height for n in row) + 2 * PAD + LABEL
        placed += [(n, i * cell_w, top, cell_w, cell_h) for i, n in enumerate(row)]
        width, top = max(width, len(row) * cell_w), top + cell_h
    sheet = Image.new("RGBA", (width, max(top, 1)), SHEET_BACKGROUND)
    for n, x, y, cell_w, cell_h in placed:
        image = images[n]
        sheet.alpha_composite(
            image, (x + (cell_w - image.width) // 2, y + cell_h - PAD - image.height)
        )
    zoomed = sheet.resize((sheet.width * ZOOM, sheet.height * ZOOM), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(zoomed)
    font = ImageFont.load_default(size=LABEL * ZOOM - 2)
    for n, x, y, _, _ in placed:
        draw.text((x * ZOOM + 4, y * ZOOM), str(n + 1), fill=(255, 255, 255, 255), font=font)
    return png(sheet), png(zoomed)


def screenshot(data: bytes) -> bytes:
    """A playtest frame at game scale, doubled when small so the reviewer sees its pixels."""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    if image.width < 640:
        image = image.resize((image.width * 2, image.height * 2), Image.Resampling.NEAREST)
    return png(image)


def motion_lines(art: ArtResult) -> str:
    """What code measured and repaired in each entity's animation rows (L2.4)."""
    lines = []
    for eid, f in art.filled.items():
        for c in f.checks:
            if c.problems or c.repaired:
                lines.append(f"* {eid}.{c.row} ({c.plays}): " + "; ".join(
                    [*c.problems, *(f"repaired: {r}" for r in c.repaired)]))  # fmt: skip
    return "\n".join(lines) or "* every row moves, keeps its size and ground line, and ends as it should"


MOTION_TASK = """
4. Motion (the filmstrips): things must move like what they are. A moving row whose frames
   barely change makes the sprite slide; a hop squashes and stretches; a break or die row ends
   with the thing gone; a form row shows the transformed look. A motion problem is major
   (blocking only when the thing cannot be recognised in it); its fix_prompt names the row."""


def review_prompt(
    design: GameDesign, art: ArtResult, slots: list[str], findings: list[Finding], shots: int,
    rows: bool = False, levels: list[int] = (),
) -> str:
    """`rows`: the last image shows every animated row as filmstrips (the motion design);
    `levels`: the screenshots are one per level, of these levels (L2.6)."""
    when = (f"one per level (levels {', '.join(map(str, levels))}, in order)" if levels
            else "early, middle, late")  # fmt: skip
    number = {eid: i + 1 for i, eid in enumerate(slots)}
    strips = f"; {3 + shots}) every animated row as a filmstrip (frames left to right, " \
             "labelled entity.row)" if rows else ""  # fmt: skip
    motion = f"\n\nMotion measured by code (rows by entity id):\n{motion_lines(art)}" if rows else ""
    entities = "\n".join(
        f"* `{e.id}` ({e.role}, {e.width}x{e.height} on a 320x180 screen): {e.look}"
        for e in sorted(design.entities, key=lambda e: e.id)
        if e.id in number
    )
    measured = (
        "\n".join(f"* sprite {number[f.entity]}: {f.check}: {f.detail}" for f in findings)
        or "* nothing flagged"
    )
    return f"""Review the art of this game as one set, as it will be played.

Images: 1) the art sheet at native game size; 2) the same sheet at {ZOOM}x, each sprite
numbered; {f"3-{2 + shots}) screenshots of the running game ({when}) at game scale" if shots else "(no screenshots: the game did not run)"}{strips}.
Sizes on the sheet are true to the game (backgrounds are shrunk).

1. Naming test first: for every number on the sheet, say in a few words what it looks like
   from its pixels alone (`named`), as a player who has not read anything about the game.
2. Score the whole set 1-5: consistency (one style: outline, shading, pixel size), readability
   (each thing reads at game size, against the floor, in crowds), perspective (every sprite
   matches the camera), scale (sizes against each other and the hero), palette (one coherent
   palette; threats, rewards and the player are easy to tell apart).
3. List concrete issues by sheet number. Severity: blocking = a player cannot tell what it is
   or cannot see it in play, a placeholder, or a sprite drawn for another camera; major =
   clearly weaker than the rest of the set or confusable with another entity; minor = polish.
   `fix_prompt` is an instruction for an image edit of that sprite: what to change (colours,
   outline, size, pose, details), keeping its framing, size and style. No issue for things
   that are fine; do not invent problems.{MOTION_TASK if rows else ""}

Camera: {VIEWS[design.camera]}.
Game: {design.title}. {design.pitch}
Look: {design.look}

The game's entities (alphabetical; the sheet numbers are not given on purpose):
{entities}

Measured by code (the sheet numbers are given here):
{measured}{motion}"""


# --- offline review: the pre-checks alone --------------------------------------------------

FIX_HINTS = {
    "contrast": "raise its contrast against the floor: a darker outline and a clearly lighter "
    "or darker body than the ground",
    "hue": "change its main colour so it no longer shares the enemy's hue",
    "size": "redraw it at its intended size, filling its frame",
    "placeholder": "draw the real sprite",
    "silhouette": "a bolder, more solid silhouette that fills its frame",
}
SCORE_OF = {"contrast": "readability", "silhouette": "readability", "hue": "palette",
            "size": "scale", "placeholder": "consistency"}  # fmt: skip


def fallback_review(slots: list[str], findings: list[Finding], design: GameDesign) -> ArtReview:
    """What the offline stand-in answers: one issue per finding (with the finding's severity),
    scores lowered per finding, no naming test (it needs eyes)."""
    number = {eid: i + 1 for i, eid in enumerate(slots)}
    looks = {e.id: e.look for e in design.entities}
    scores = dict.fromkeys(Scores.model_fields, 5)
    issues = []
    for f in findings:
        if f.entity not in number:
            continue
        scores[SCORE_OF[f.check]] = max(1, scores[SCORE_OF[f.check]] - 1)
        hint = FIX_HINTS[f.check] + (f": {looks[f.entity]}" if f.check == "placeholder" else "")
        issues.append(ReviewIssue(slot=number[f.entity], problem=f"{f.check}: {f.detail}",
                                  severity=f.severity, fix_prompt=hint))  # fmt: skip
    return ArtReview(scores=Scores(**scores), named=[], issues=issues)


# --- from the answer to entity issues ------------------------------------------------------


def vocabulary(e: Entity, f: Filled) -> set[str]:
    """Words that name what an entity is: its id, its look and its library source."""
    text = f"{e.id.replace('_', ' ')} {e.look}"
    if f.source.startswith("library "):
        text += " " + f.source.split(" ", 1)[1].split("/")[-1].replace("-", " ")
    return meaningful(text)


def meaningful(text: str) -> set[str]:
    found = [w for w in words(text) if w not in VAGUE and w not in COLORS and len(w) > 2]
    stems = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in found}
    return stems | canon(stems) | canon(found)


def named_ok(seen_as: str, vocab: set[str]) -> bool:
    return bool(meaningful(seen_as) & vocab)


@dataclass
class Issue:
    n: int
    entity: str
    problem: str
    severity: Severity
    fix_prompt: str
    origin: str  # review | naming test | pre-check
    status: str = "suggestion"  # fixed | not fixed: <why> | suggestion

    def dump(self) -> dict:
        return {"n": self.n, "entity": self.entity, "problem": self.problem,
                "severity": self.severity, "fix_prompt": self.fix_prompt,
                "from": self.origin, "status": self.status}  # fmt: skip


def entity_issues(
    answer: ArtReview, slots: list[str], design: GameDesign, art: ArtResult
) -> tuple[list[Issue], list[dict], list[str]]:
    """(issues, naming results, notes). Model issues first, then what code adds."""
    entities = {e.id: e for e in design.entities}
    notes, issues, named = [], [], []
    for i in answer.issues:
        if not 1 <= i.slot <= len(slots):
            notes.append(f"issue on unknown sprite number {i.slot} ignored: {i.problem}")
            continue
        issues.append(Issue(0, slots[i.slot - 1], i.problem, i.severity, i.fix_prompt, "review"))
    flagged = {i.entity for i in issues if i.severity in {"blocking", "major"}}
    for m in answer.named:
        if not 1 <= m.slot <= len(slots):
            continue
        eid = slots[m.slot - 1]
        ok = named_ok(m.seen_as, vocabulary(entities[eid], art.filled[eid]))
        named.append({"entity": eid, "seen_as": m.seen_as, "ok": ok})
        if not ok and eid not in flagged:
            look = entities[eid].look
            issues.append(Issue(0, eid, f"reads as '{m.seen_as}', not as {eid} ({look})",
                                "major", f"make it read clearly as {look}", "naming test"))  # fmt: skip
            flagged.add(eid)
    blocking = {i.entity for i in issues if i.severity == "blocking"}
    for eid, f in art.filled.items():
        if f.source == "placeholder" and eid not in blocking:
            look = entities[eid].look
            issues.append(Issue(0, eid, "greybox placeholder, not real art", "blocking",
                                f"draw the real sprite: {look}", "pre-check"))  # fmt: skip
    for n, issue in enumerate(issues, 1):
        issue.n = n
    return issues, named, notes


# --- fixes ---------------------------------------------------------------------------------


def ai_allowed(pick: ArtPick, camera: str) -> bool:
    """May this art's pixels be sent to Muse? Library art only when its pack allows AI use."""
    from spriteforge.library.store import load_packs

    if pick.source == "object":
        return bool(whole_objects()[pick.asset].get("ai_allowed", True))
    if pick.source == "actor":
        pack = actor_index()[pick.asset].pack
    elif pick.source == "tile":
        pack = tile_ids(load_tilesets(camera))[pick.asset][0]["pack"]
    elif pick.source == "backdrop":
        pack = next(ts["pack"] for ts in load_tilesets(camera) if ts["id"] == pick.asset)
    else:
        return True  # Muse art
    packs = {p.id: p for p in load_packs()}
    return pack in packs and packs[pack].ai_allowed


def fix_cap(max_fixes: int | None, cost_cap: float | None, review_cost: float,
            image_cost: float) -> int | None:  # fmt: skip
    """How many fixes fit: at most `max_fixes`, and the review call plus one image call per
    fix within `cost_cap`. None: no fixed cap (the Art Reviewer's allowance holds)."""
    fit = (None if cost_cap is None or image_cost <= 0
           else max(0, math.floor((cost_cap - review_cost) / image_cost + 1e-9)))  # fmt: skip
    caps = [c for c in (max_fixes, fit) if c is not None]
    return min(caps) if caps else None


def plan_fixes(
    issues: list[Issue], design: GameDesign, art: ArtResult, cap: int | None, why: str = ""
) -> list[str]:
    """Entities to fix, in priority order (player first), at most `cap`; marks the status of
    every blocking issue that is not fixed (`why`: what set the cap)."""
    role = {e.id: e.role for e in design.entities}
    blocking = sorted(
        (i for i in issues if i.severity == "blocking"),
        key=lambda i: (PRIORITY.index(role[i.entity]), i.n),
    )
    chosen: list[str] = []
    for i in blocking:
        if art.filled[i.entity].extra:
            i.status = "not fixed: a parallax backdrop (the code uses its layers); apply it"
        elif i.entity in chosen:
            continue
        elif cap is not None and len(chosen) >= cap:
            i.status = f"not fixed: {why or f'fix cap ({cap}) reached'}"
        else:
            chosen.append(i.entity)
    return chosen


@dataclass
class Fix:
    entity: str
    issues: list[int]
    pick: ArtPick  # the request as a plan pick (reason marks it as a review fix)
    source_png: bytes | None  # the current image the edit starts from; None: a fresh draw
    mode: str
    before: Filled
    after: Filled | None = None
    notes: list[str] = field(default_factory=list)


def edit_source(f: Filled) -> bytes:
    """The current image for an edit: Muse's raw output when there is one, else the first frame
    scaled up (nearest) to at least 256 px."""
    if f.record.get("raw"):
        return f.record["raw"]
    image = first_frame(f)
    k = max(1, math.ceil(256 / max(image.size)))
    return png(image.resize((image.width * k, image.height * k), Image.Resampling.NEAREST))


def fix_requests(
    entities: list[str], issues: list[Issue], design: GameDesign, art: ArtResult
) -> list[Fix]:
    by_id = {e.id: e for e in design.entities}
    fixes = []
    for eid in entities:
        mine = [i for i in issues if i.entity == eid and i.severity == "blocking"]
        e, f, p = by_id[eid], art.filled[eid], art.picks[eid]
        base = p.prompt.strip() if p.source == "muse" and p.prompt.strip() else e.look
        problems = "; ".join(i.problem for i in mine)
        fix_text = "; ".join(i.fix_prompt for i in mine)
        if f.source == "placeholder":
            source, mode = None, "fresh draw (it was a placeholder)"
        elif p.source == "muse" or ai_allowed(p, design.camera):
            source = edit_source(f)
            mode = "edit of the Muse image" if p.source == "muse" else "edit of the library art"
        else:
            source, mode = None, "fresh draw: its library pack forbids AI use, so it is not sent"
        pick = ArtPick(
            entity=eid,
            source="muse",
            asset="",
            prompt=f"{base}. Fix: {fix_text}",
            reference=p.reference if p.source == "muse" else "",
            recolor="",
            reason=f"{REVIEW_REASON} {problems}",
        )
        fixes.append(Fix(eid, [i.n for i in mine], pick, source, mode, f))
    return fixes


def reviewed_plan(plan: ArtPlan, fixes: list[Fix], every: bool = False) -> ArtPlan:
    """The plan with each kept fix (`every`: each fix) as its Muse pick, so a later version
    asks for the same thing and locks the fixed art."""
    picks = {p.entity: p for p in plan.picks}
    for fx in fixes:
        if every or fx.after is not None:
            picks[fx.entity] = fx.pick
    return plan.model_copy(update={"picks": list(picks.values())})


def run_fixes(
    fixes: list[Fix],
    design: GameDesign,
    plan: ArtPlan,
    art: ArtResult,
    *,
    generate,
    options: MuseOptions,
    cache: ImageCache | None,
    version: int,
) -> list[str]:
    """Draw every fix once, in parallel (one call slot each, `options.retries` should be 0). A
    fix that fails keeps the current art. Sets `fix.after`; returns warnings."""
    # A later version resolves the reviewed plan the same way, so the fixed art locks.
    motion = art.motion
    resolved, _ = resolve_plan(reviewed_plan(plan, fixes, every=True), design, motion)
    entities = {e.id: e for e in design.entities}
    forms = {f.ability: f.looks for f in motion.brief.forms} if motion else {}
    hero_px = next(e.height for e in design.entities if e.role == "player") * art.scale
    slots, jobs = CallSlots(len(fixes)), []
    for fx in fixes:
        fx.pick = resolved[fx.entity]
        e = entities[fx.entity]
        size = art.screen if e.role == "background" else (e.width * art.scale, e.height * art.scale)
        old = {"raw": fx.source_png, "version": version} if fx.source_png else None
        muse = Muse(generate, lambda eid, o=old: o, slots, options, hero_px, cache,
                    brief=motion.rows(e.id) if motion else [], forms=forms)  # fmt: skip
        slots.take()
        jobs.append((muse, replace(muse.job(e, design, fx.pick, size), slot=True)))
    drawn = run_jobs(jobs, options.workers)
    warnings = []
    for (muse, _), fx in zip(jobs, fixes, strict=True):
        warnings += muse.warnings
        out = drawn[fx.entity]
        if out.source == "placeholder":  # the call failed or drew nothing usable
            fx.notes.append("the image call failed; the art is unchanged")
            continue
        out.source = "muse (review fix)"
        out.reasons = [
            *[r for r in out.reasons if not re.match(r"edited from v\d+ per feedback", r)],
            f"fixed by the art review ({fx.mode}): {fx.pick.reason[len(REVIEW_REASON) :].strip()}",
        ]
        out.record["review_fix"] = {"issues": fx.issues, "mode": fx.mode}
        if motion and entities[fx.entity].role == "tile":
            tile_break(entities[fx.entity], out, motion.rows(fx.entity))
        fx.after = out
    return warnings


def thumb(f: Filled) -> bytes:
    """A before/after picture for G4: the first frame at 4x."""
    image = sheet_image(f)
    return png(image.resize((image.width * ZOOM, image.height * ZOOM), Image.Resampling.NEAREST))
