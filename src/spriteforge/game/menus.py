"""Menus as data (L2.5): the Menu Designer writes them, code fits and checks them, the SDK
renders them (`sdk/sfflow.js`) in the game's style.

`MenuSpec` (strict; Menu Designer, Muse Spark) is the theme (colours the SDK draws panels and
text with), every screen with its title, subtitle and items (a label and an action), the
credits, and what Muse Image draws for the title background and the emblem (no text: the SDK
draws all text). Screens: title (press Enter), main, pause, settings, controls, credits,
victory, and with a way to lose game_over, and with several levels results and level_select
(its level items come from the saved progress; the spec gives its other items).

The Menu Designer writes the screens and a colour intent from the design (at G2 approval the
art is not drawn yet); `fit_theme` snaps the theme to the colours measured from the build's
locked art and repairs text contrast. `fit_menu` repairs what code can (unknown actions with a
known meaning, missing screens and items, overlong labels); `menu_problems` is the validator:
every screen reachable from the title, every action known and allowed where it is, a way out
of every screen (Escape is the SDK's: back to the screen before, resume on pause, the title
after a level ends), every label fitting the font on its panel, readable text on its panel.
"""

import colorsys
import io
import re

import numpy as np
from PIL import Image
from pydantic import Field

from spriteforge.agents.models import Contract
from spriteforge.library.looks import colour_gap, hex_of, measure, rgb_of

from .art import look_palette
from .design import GameDesign
from .structure import GameStructure

SCREENS = ("title", "main", "pause", "settings", "controls", "credits", "level_select",
           "results", "game_over", "victory")  # fmt: skip
ACTIONS = {
    "main": "open the main menu (the title screen's prompt)",
    "start": "a new game from level 1",
    "continue": "play on from the last level unlocked",
    "level_select": "open the level select",
    "settings": "open the settings",
    "controls": "open the controls",
    "credits": "open the credits",
    "resume": "close the pause menu",
    "restart": "start the current level again",
    "retry": "play the level just lost again",
    "next_level": "play the next level",
    "title": "back to the title screen",
    "back": "back to the screen before",
    "difficulty": "cycle easy / normal / hard",
    "fewer_flashes": "toggle fewer flashes (no screen flash or shake)",
    "sound": "toggle the sound on / off (M does it anywhere)",
}
# Words a model may use for an action, mapped to the action they mean.
SYNONYMS = {
    "play": "start", "new_game": "start", "new": "start", "begin": "start", "start_game": "start",
    "load": "continue", "resume_game": "resume", "unpause": "resume", "options": "settings",
    "quit": "title", "quit_to_title": "title", "exit": "title", "menu": "title",
    "main_menu": "main", "press_start": "main", "press_enter": "main", "levels": "level_select",
    "select_level": "level_select", "try_again": "retry", "restart_level": "restart",
    "next": "next_level", "continue_level": "next_level", "return": "back", "close": "back",
    "help": "controls", "how_to_play": "controls", "flashes": "fewer_flashes",
    "reduce_flashes": "fewer_flashes", "set_difficulty": "difficulty", "audio": "sound",
    "mute": "sound", "sfx": "sound", "sound_effects": "sound", "toggle_sound": "sound",
}  # fmt: skip
# What each screen may offer, and what it must offer (the first item is the default).
ALLOWED = {
    "title": {"main", "start"},
    "main": {"start", "continue", "level_select", "settings", "controls", "credits"},
    "pause": {"resume", "restart", "settings", "controls", "title"},
    "settings": {"difficulty", "fewer_flashes", "sound", "controls", "back"},
    "controls": {"back"},
    "credits": {"back"},
    "level_select": {"back"},
    "results": {"next_level", "restart", "title"},
    "game_over": {"retry", "title"},
    "victory": {"credits", "title", "start"},
}
REQUIRED = {
    "title": ["main"], "main": ["start", "settings", "credits"],
    "pause": ["resume", "restart", "title"], "settings": ["difficulty", "fewer_flashes", "sound", "back"],
    "controls": ["back"], "credits": ["back"], "level_select": ["back"],
    "results": ["next_level", "title"], "game_over": ["retry", "title"],
    "victory": ["title", "credits"],
}  # fmt: skip
LABELS = {
    "main": "Press Enter", "start": "Start", "continue": "Continue", "level_select": "Level select",
    "settings": "Settings", "controls": "Controls", "credits": "Credits", "resume": "Resume",
    "restart": "Restart level", "retry": "Retry", "next_level": "Next level",
    "title": "Quit to title", "back": "Back", "difficulty": "Difficulty",
    "fewer_flashes": "Fewer flashes", "sound": "Sound",
}  # fmt: skip
TITLES = {
    "title": "", "main": "", "pause": "Paused", "settings": "Settings", "controls": "Controls",
    "credits": "Credits", "level_select": "Level select", "results": "Level clear",
    "game_over": "Game over", "victory": "Victory",
}  # fmt: skip
DIFFICULTY = {"easy": 0.75, "normal": 1.0, "hard": 1.35}
VALUED = {"difficulty", "fewer_flashes", "sound"}  # settings the SDK shows with their value
MAX_ITEMS = 7
# The SDK's text: a monospace font at 8 px (items), 12 px (screen titles), 16 px (the game's
# title on the title screen), times the art scale; a character is at most 0.62 em wide.
CHAR_EM, ITEM_PX, TITLE_PX, BIG_PX = 0.62, 8, 12, 16
PANEL_MAX, PANEL_PAD = 260, 10  # panel width cap and inner padding (px at scale 1)
MARK = 2  # characters of the highlight marker ("> ") and the value shown after settings
VALUE = len(": normal")


class MenuItem(Contract):
    label: str = Field(description="what the player reads, short (e.g. 'Start', 'Settings')")
    action: str = Field(description="one of: " + ", ".join(ACTIONS))


class Screen(Contract):
    id: str = Field(description="one of: " + ", ".join(SCREENS))
    title: str = Field(description="the heading ('' on the title and main screens: the game's "
                                   "title is shown there)")  # fmt: skip
    subtitle: str = Field(description="one short line under the heading, or ''")
    items: list[MenuItem] = Field(description="in order; the first is selected when it opens")


class Theme(Contract):
    panel: str = Field(description="#RRGGBB of the menu panel (dark, from the game's look)")
    border: str = Field(description="#RRGGBB of the panel's frame")
    text: str = Field(description="#RRGGBB of item text (readable on the panel)")
    highlight: str = Field(description="#RRGGBB of the selected item and headings")
    dim: str = Field(description="#RRGGBB of locked items (locked levels)")
    style: str = Field(description="panel style: framed (a 1 px frame), double (two frames) or solid")


class MenuSpec(Contract):
    title: str = Field(description="the game's title as the title screen shows it")
    subtitle: str = Field(description="a short tagline under the title, or ''")
    theme: Theme
    screens: list[Screen]
    credits: list[str] = Field(description="leave [] (code writes the credits: what made the "
                                           "game and the art's credits; never invent people or "
                                           "music)")  # fmt: skip
    title_art: str = Field(description="what the title screen's background picture shows (a "
                                       "scene from the game; never text)")  # fmt: skip
    emblem: str = Field(description="the emblem over the title: one object or creature of the "
                                    "game on a plain background (never text)")  # fmt: skip

    def screen(self, sid: str) -> Screen | None:
        return next((s for s in self.screens if s.id == sid), None)


# --- what a game needs ------------------------------------------------------------------------


def needed_screens(structure: GameStructure) -> list[str]:
    out = ["title", "main", "pause", "settings", "controls", "credits", "victory"]
    if structure.lose.strip():
        out.append("game_over")
    if len(structure.levels) > 1:
        out += ["results", "level_select"]
    return [s for s in SCREENS if s in out]


def allowed(sid: str, structure: GameStructure) -> set[str]:
    acts = set(ALLOWED.get(sid, set()))
    if len(structure.levels) < 2:
        acts -= {"continue", "level_select", "next_level"}
    return acts


def required(sid: str, structure: GameStructure) -> list[str]:
    return [a for a in REQUIRED.get(sid, []) if a in allowed(sid, structure)]


def action_of(text: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return key if key in ACTIONS else SYNONYMS.get(key, key)


# --- the font and the panel -------------------------------------------------------------------


def panel_width(screen_w: int, scale: int = 1) -> float:
    return min(screen_w / scale - 32, PANEL_MAX)


def text_width(text: str, px: int) -> float:
    return len(text) * px * CHAR_EM


def max_chars(screen_w: int, scale: int, px: int, extra: int = 0) -> int:
    room = panel_width(screen_w, scale) - 2 * PANEL_PAD
    return int(room // (px * CHAR_EM)) - extra


def fits(text: str, screen_w: int, scale: int, px: int, extra: int = 0) -> bool:
    return len(text) <= max_chars(screen_w, scale, px, extra)


def clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit)].rsplit(" ", 1)[0]
    return cut if len(cut) >= limit // 2 else text[:limit]


# --- colours and contrast -----------------------------------------------------------------------

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def luminance(hex_code: str) -> float:
    rgb = np.array(rgb_of(hex_code)) / 255
    lin = np.where(rgb <= 0.03928, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return float(lin @ np.array([0.2126, 0.7152, 0.0722]))


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two colours (1-21)."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def saturation(hex_code: str) -> float:
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb_of(hex_code)))[1]


def value(hex_code: str) -> float:
    return max(rgb_of(hex_code)) / 255


def mix(a: str, b: str, t: float) -> str:
    return hex_of(np.array(rgb_of(a)) * (1 - t) + np.array(rgb_of(b)) * t)


def readable(colour: str, panel: str, ratio: float) -> str:
    """`colour`, moved toward white (or black on a light panel) until it reads on `panel`."""
    if contrast(colour, panel) >= ratio:
        return colour.upper()
    toward = "#FFFFFF" if luminance(panel) < 0.4 else "#000000"
    for step in range(1, 21):
        c = mix(colour, toward, step / 20)
        if contrast(c, panel) >= ratio:
            return c
    return toward


MIN_TEXT, MIN_HIGHLIGHT, MIN_DIM, MIN_BORDER = 4.5, 3.0, 1.6, 1.4
SNAP_GAP = 25  # delta E: an art colour this close to the designer's stands in for it


def default_theme(look: str = "") -> Theme:
    named = look_palette(look)
    accent = next((c for c in named if 0.15 < luminance(c) < 0.8), "#F4D35E")
    return Theme(panel="#10121C", border=accent, text="#E8E8F0", highlight=accent, dim="#6A6F80",
                 style="framed")  # fmt: skip


def art_colours(pngs: list[bytes]) -> list[tuple[str, float]]:
    """(hex, weight) of the main colours measured from the locked art, all images together."""
    weights: dict[str, float] = {}
    for data in pngs:
        try:
            look = measure(Image.open(io.BytesIO(data)))
        except Exception:  # noqa: BLE001, S112 -- an unreadable image adds no colours
            continue
        for c in look.colours:
            weights[c.hex] = weights.get(c.hex, 0) + c.share
    return sorted(weights.items(), key=lambda kv: -kv[1])


def nearest(colour: str, palette: list[str]) -> str:
    target = np.array(rgb_of(colour), dtype=float)
    return min(palette, key=lambda c: float(np.sum((np.array(rgb_of(c)) - target) ** 2)))


def fit_theme(theme: Theme, colours: list[tuple[str, float]]) -> tuple[Theme, list[str]]:
    """The theme in the art's measured colours: the panel is the art's darkest colour (darkened
    to a panel), the highlight and frame the art colour nearest the designer's, then text,
    highlight, dim and frame are made readable on the panel. Without art, only contrast."""
    notes = []
    t = theme.model_copy()
    for name in ("panel", "border", "text", "highlight", "dim"):
        if not HEX.match(getattr(t, name)):
            setattr(t, name, getattr(default_theme(), name))
            notes.append(f"theme {name} was not a #RRGGBB colour: the default is used")
    if t.style not in {"framed", "double", "solid"}:
        notes.append(f"panel style {t.style!r} is not framed, double or solid: framed is used")
        t.style = "framed"
    palette = [c for c, _ in colours]
    if palette:
        darkest = min(palette, key=luminance)
        t.panel = mix(darkest, "#000000", 0.55) if luminance(darkest) > 0.02 else darkest
        # The highlight is the art's colourful colour nearest the designer's: the selected item
        # must stand out from the plain text, not just be readable.
        vivid = [c for c in palette if saturation(c) >= 0.3 and value(c) >= 0.35]
        want = theme.highlight if HEX.match(theme.highlight) else "#F4D35E"
        near = min(vivid, key=lambda c: colour_gap(c, want)) if vivid else None
        # Snap to the art only when it has a colour like the one asked for; a colour the art does
        # not have stays (live 2026-11-02: "acid green" was snapped to the art's gold).
        t.highlight = near if near and colour_gap(near, want) <= SNAP_GAP else want
        if t.highlight == want and near:
            notes.append(f"highlight {want} kept: the art has no colour like it (nearest {near})")
        t.border = mix(t.highlight, t.panel, 0.3)
        lightest = max(palette, key=luminance)
        t.text = mix(lightest, "#FFFFFF", 0.5)
        t.dim = mix(t.text, t.panel, 0.55)
        notes.append("theme colours taken from the locked art's measured colours")
    # The highlight reads at least as well as item text (4.5); the validator accepts 3.
    for name, ratio in (("text", MIN_TEXT), ("highlight", MIN_TEXT), ("dim", MIN_DIM),
                        ("border", MIN_BORDER)):  # fmt: skip
        before = getattr(t, name)
        after = readable(before, t.panel, ratio)
        if after.upper() != before.upper():
            setattr(t, name, after)
            notes.append(f"theme {name} {before} was hard to read on the panel: {after}")
    for name in ("panel", "border", "text", "highlight", "dim"):
        setattr(t, name, getattr(t, name).upper())
    return t, notes


class MenuRecord(Contract):
    """The menus of a game as the run keeps them (game/menus/vM.json): the spec fitted by
    code, the structure it was fitted to, what code repaired and what it could not."""

    spec: MenuSpec
    structure: GameStructure
    notes: list[str]
    problems: list[str]
    retried: bool
    by: str
    why: str = ""  # "written from design v1", "G3: <the human's words>"


# --- the offline Menu Designer, fit and the validator -------------------------------------------


def fallback_menu(design: GameDesign, structure: GameStructure) -> MenuSpec:
    """Offline Menu Designer: every screen the game needs with its plain labels, a theme from
    the design's look words."""
    screens = []
    for sid in needed_screens(structure):
        acts = list(REQUIRED.get(sid, []))
        if sid == "main":
            acts = ["start", "continue", "level_select", "settings", "controls", "credits"]
        if sid == "pause":
            acts = ["resume", "restart", "settings", "title"]
        if sid == "victory":  # Enter plays again (as Enter after a win always did)
            acts = ["start", "credits", "title"]
        acts = [a for a in acts if a in allowed(sid, structure)]
        subtitle = {"game_over": design.lose, "victory": structure.win}.get(sid, "")
        label = lambda a: "Play again" if (sid, a) == ("victory", "start") else LABELS[a]
        screens.append(Screen(id=sid, title=TITLES[sid], subtitle=subtitle[:60],
                              items=[MenuItem(label=label(a), action=a) for a in acts]))  # fmt: skip
    return MenuSpec(title=design.title, subtitle=design.pitch.split(".")[0][:60],
                    theme=default_theme(design.look), screens=screens,
                    credits=credit_lines(design),
                    title_art=f"{design.world}; {design.look}",
                    emblem=next((f"{e.look}" for e in design.entities if e.role == "player"), design.title))  # fmt: skip


def fit_menu(spec: MenuSpec, structure: GameStructure, design: GameDesign,
             screen_w: int = 320, scale: int = 1) -> tuple[MenuSpec, list[str]]:  # fmt: skip
    """Repair what code can: actions named by a synonym, items not allowed where they are (or
    in this game), duplicates, missing screens and items, empty or overlong labels and titles,
    theme colours that are no colours."""
    notes: list[str] = []
    need = needed_screens(structure)
    fallback = fallback_menu(design, structure)
    by_id: dict[str, Screen] = {}
    for s in spec.screens:
        sid = action_of(s.id) if s.id not in SCREENS else s.id
        sid = {"gameover": "game_over", "levels": "level_select", "options": "settings",
               "win": "victory", "help": "controls"}.get(sid, sid)  # fmt: skip
        if sid not in need:
            notes.append(f"screen {s.id!r} dropped: " + ("not a screen the SDK has" if sid not in SCREENS
                                                       else "this game does not need it"))  # fmt: skip
            continue
        if sid in by_id:
            notes.append(f"a second {sid} screen dropped")
            continue
        by_id[sid] = s.model_copy(update={"id": sid})
    screens = []
    item_room = max_chars(screen_w, scale, ITEM_PX, MARK)
    for sid in need:
        s = by_id.get(sid)
        if s is None:
            notes.append(f"screen {sid} was missing: added with its plain items")
            s = fallback.screen(sid)
        items, seen = [], set()
        for it in s.items:
            act = action_of(it.action)
            if act not in ACTIONS:
                notes.append(f"{sid}: item {it.label!r} dropped: unknown action {it.action!r}")
                continue
            if act not in allowed(sid, structure) or act in seen:
                why = "repeated" if act in seen else "not allowed on this screen in this game"
                notes.append(f"{sid}: item {it.label!r} ({act}) dropped: {why}")
                continue
            seen.add(act)
            room = item_room - (VALUE if act in VALUED else 0)
            said = it.label
            if act in VALUED and SETTING_VALUE.search(said):
                # The SDK shows the setting's value after the label (live 2026-11-02: the Menu
                # Designer wrote "Difficulty: Normal", which would read "Difficulty: Normal: hard").
                said = SETTING_VALUE.sub("", said)
                notes.append(f"{sid}: label {it.label!r} carried its value: {said!r} (the SDK adds it)")
            label = clip(said, room) or LABELS[act]
            if label != re.sub(r"\s+", " ", said).strip():
                notes.append(f"{sid}: label {it.label!r} shortened to {label!r} to fit the panel")
            items.append(MenuItem(label=label, action=act))
        for act in required(sid, structure):
            if act not in seen:
                notes.append(f"{sid}: the {act} item was missing: added")
                ways_out = [i for i, x in enumerate(items) if x.action in {"back", "title"}]
                at = ways_out[0] if ways_out and act not in {"back", "title"} else len(items)
                items.insert(at, MenuItem(label=LABELS[act], action=act))
                seen.add(act)
        if len(items) > MAX_ITEMS:
            notes.append(f"{sid}: {len(items)} items, at most {MAX_ITEMS}: the last ones dropped")
            keep = set(required(sid, structure))
            extra = [i for i in items if i.action not in keep][: MAX_ITEMS - len(keep)]
            items = [i for i in items if i.action in keep or i in extra]
        title = clip(s.title, max_chars(screen_w, scale, TITLE_PX))
        subtitle = clip(s.subtitle, max_chars(screen_w, scale, ITEM_PX))
        screens.append(Screen(id=sid, title=title, subtitle=subtitle, items=items))
    # A screen nothing opens gets an item that opens it (main first, then settings, pause).
    probe = spec.model_copy(update={"screens": screens})
    for sid in [x for x in need if x not in reachable(probe, structure)]:
        host = next((h for h in ("main", "settings", "pause") if sid in allowed(h, structure)
                     and len(probe.screen(h).items) < MAX_ITEMS), None)  # fmt: skip
        if host:
            items = probe.screen(host).items
            back = [i for i in items if i.action in {"back", "title"}]
            rest = [i for i in items if i not in back]
            probe.screen(host).items = [*rest, MenuItem(label=LABELS[sid], action=sid), *back]
            notes.append(f"the {sid} screen could not be reached: {host} opens it now")
    title = clip(spec.title, max_chars(screen_w, scale, BIG_PX)) or clip(design.title, max_chars(screen_w, scale, BIG_PX))
    if title != spec.title.strip():
        notes.append(f"title {spec.title!r} shown as {title!r}")
    theme, theme_notes = fit_theme(spec.theme, [])
    fitted = spec.model_copy(update={
        "title": title, "subtitle": clip(spec.subtitle, max_chars(screen_w, scale, ITEM_PX)),
        "theme": theme, "screens": screens,
        "credits": credit_lines(design),
        "title_art": spec.title_art.strip()[:400] or fallback.title_art,
        "emblem": spec.emblem.strip()[:300] or fallback.emblem,
    })  # fmt: skip
    said = [c.strip() for c in spec.credits if c.strip()]
    if said and said != credit_lines(design):
        notes.append("credits are written by code; the designer's lines were dropped: "
                     + "; ".join(said)[:200])  # fmt: skip
    return fitted, notes + theme_notes


SETTING_VALUE = re.compile(r"\s*[:=-]?\s*\(?\b(easy|normal|hard|on|off)\b\)?\s*$", re.IGNORECASE)


def credit_lines(design: GameDesign) -> list[str]:
    """The credits code writes: only what is true of every SpriteForge game (the art's credits
    are added per build). A model's credits invented people and music (live 2026-11-02: "Made
    by the Goo Squad", "Music: Chiptune Blips" for a game without sound)."""
    return [clip(design.title, 36), "Made with SpriteForge", "Design and code: Muse Spark",
            "Art: Muse Image and the art library"]  # fmt: skip


def reachable(spec: MenuSpec, structure: GameStructure) -> set[str]:
    """Screens a player can reach from the title: through items, and through play (Escape
    pauses, finishing a level shows results, losing game over, the last level victory)."""
    opens = {"main": "main", "level_select": "level_select", "settings": "settings",
             "controls": "controls", "credits": "credits", "title": "title"}  # fmt: skip
    plays = {"start", "continue", "resume", "restart", "retry", "next_level"}
    have = {s.id for s in spec.screens}
    seen, todo, playing = set(), ["title"] if "title" in have else [], False
    while todo:
        sid = todo.pop()
        if sid in seen:
            continue
        seen.add(sid)
        s = spec.screen(sid)
        for it in s.items if s else []:
            if it.action in opens and opens[it.action] in have:
                todo.append(opens[it.action])
            playing = playing or it.action in plays
        if playing:
            todo += [x for x in ("pause", "results", "game_over", "victory") if x in have]
            if len(structure.levels) < 2:
                todo = [x for x in todo if x != "results"]
            if not structure.lose.strip():
                todo = [x for x in todo if x != "game_over"]
    return seen


ESCAPE = {"pause": "resume", "results": "title", "game_over": "title", "victory": "title"}
EXITS = {"back", "resume", "title", "retry", "next_level", "start", "continue", "restart", "main"}


def menu_problems(spec: MenuSpec, structure: GameStructure, screen_w: int = 320,
                  scale: int = 1) -> list[str]:  # fmt: skip
    """What is wrong with a menu spec (empty when the SDK can render it as the rules say)."""
    out = []
    ids = [s.id for s in spec.screens]
    for sid in ids:
        if sid not in SCREENS:
            out.append(f"unknown screen {sid!r}")
    for sid in needed_screens(structure):
        if sid not in ids:
            out.append(f"the {sid} screen is missing")
    for sid in sorted(set(ids) & set(SCREENS) - reachable(spec, structure)):
        out.append(f"the {sid} screen cannot be reached from the title")
    item_room = max_chars(screen_w, scale, ITEM_PX, MARK)
    for s in spec.screens:
        acts = [it.action for it in s.items]
        for it in s.items:
            if it.action not in ACTIONS:
                out.append(f"{s.id}: item {it.label!r} has an unknown action {it.action!r}")
            elif s.id in SCREENS and it.action not in allowed(s.id, structure):
                out.append(f"{s.id}: item {it.label!r} ({it.action}) is not allowed there")
            room = item_room - (VALUE if it.action in VALUED else 0)
            if not it.label.strip():
                out.append(f"{s.id}: an item has no label")
            elif len(it.label) > room:
                out.append(f"{s.id}: label {it.label!r} is {len(it.label)} characters; "
                           f"{room} fit the panel")  # fmt: skip
        for act in required(s.id, structure) if s.id in SCREENS else []:
            if act not in acts:
                out.append(f"{s.id}: the {act} item is missing")
        if s.id in SCREENS and s.id != "title" and not (set(acts) & EXITS) and s.id not in ESCAPE:
            out.append(f"{s.id}: no item leads out of it (back, resume or title)")
        if s.id in ESCAPE and ESCAPE[s.id] == "title" and "title" not in ids:
            out.append(f"{s.id}: Escape leads to the title screen, which is missing")
        if len(s.title) > max_chars(screen_w, scale, TITLE_PX):
            out.append(f"{s.id}: the heading {s.title!r} does not fit the panel")
        if len(s.items) > MAX_ITEMS:
            out.append(f"{s.id}: {len(s.items)} items; at most {MAX_ITEMS}")
    if not spec.title.strip():
        out.append("the title screen has no title")
    elif len(spec.title) > max_chars(screen_w, scale, BIG_PX):
        out.append(f"the title {spec.title!r} does not fit the screen")
    t = spec.theme
    colours = {n: getattr(t, n) for n in ("panel", "border", "text", "highlight", "dim")}
    bad = [n for n, c in colours.items() if not HEX.match(c)]
    out += [f"theme {n} {colours[n]!r} is not a #RRGGBB colour" for n in bad]
    if not bad:
        for name, ratio in (("text", MIN_TEXT), ("highlight", MIN_HIGHLIGHT), ("dim", MIN_DIM)):
            got = contrast(colours[name], t.panel)
            if got < ratio:
                out.append(f"theme {name} {colours[name]} on the panel {t.panel} has contrast "
                           f"{got:.1f}; at least {ratio:g} reads")  # fmt: skip
    return out


def checked(spec: MenuSpec, structure: GameStructure, design: GameDesign, by: str,
            retried: bool = False, notes: list[str] = (), why: str = "",
            screen_w: int = 320, scale: int = 1) -> MenuRecord:  # fmt: skip
    """A Menu Designer's answer fitted and checked by code."""
    fitted, repairs = fit_menu(spec, structure, design, screen_w, scale)
    return MenuRecord(spec=fitted, structure=structure, notes=[*notes, *repairs],
                      problems=menu_problems(fitted, structure, screen_w, scale),
                      retried=retried, by=by, why=why)  # fmt: skip


def refit(record: MenuRecord, structure: GameStructure, design: GameDesign) -> MenuRecord:
    """The same menus for a changed structure (a design change: more or fewer levels, a way to
    lose or none), fitted again by code; no model call."""
    if record.structure == structure:
        return record
    again = checked(record.spec, structure, design, record.by, record.retried, record.notes,
                    f"refitted to design changes ({record.why})")  # fmt: skip
    return again


def retry_prompt(prompt: str, problems: list[str]) -> str:
    return (prompt + "\n\nCode checked your answer and found these problems; answer again with "
            "them fixed and everything else kept:\n" + "\n".join(f"* {p}" for p in problems))


# --- the Menu Designer's prompts -------------------------------------------------------------


def menu_rules(structure: GameStructure, screen_w: int = 320, scale: int = 1) -> str:
    screens = "\n".join(
        f"* {sid}: actions {', '.join(sorted(allowed(sid, structure)))}; must have "
        f"{', '.join(required(sid, structure))}"
        for sid in needed_screens(structure)
    )
    actions = "\n".join(f"* {a}: {what}" for a, what in ACTIONS.items())
    return f"""The SDK renders every screen with its built-in monospace pixel font in the theme's
colours and handles the keys: arrows/WASD move the highlight, Enter/Space pick, Escape goes back
(on pause it resumes; after a level ends it goes to the title), the mouse works too. It shows the
title art behind the title and main screens, and the level's own picture behind pause, results,
game over and victory. Level select lists the levels from the saved progress (locked ones dim);
give it only a Back item. Settings items show their value after the label (Difficulty: normal):
write the label alone ('Difficulty', never 'Difficulty: Normal'). Code writes the credits.

Screens this game has (only these):
{screens}

Actions:
{actions}

Limits: at most {MAX_ITEMS} items a screen; item labels at most
{max_chars(screen_w, scale, ITEM_PX, MARK)} characters ({max_chars(screen_w, scale, ITEM_PX, MARK) - VALUE} for difficulty, fewer_flashes and sound), headings at most
{max_chars(screen_w, scale, TITLE_PX)}, the game's title at most {max_chars(screen_w, scale, BIG_PX)}. Text must read on the
panel (contrast 4.5 for item text, 3 for the highlight). Code fits the theme to the colours of the
game's locked art; give the colour intent from the design's look.
Write labels in the game's voice but keep their meaning plain (a player must know what 'Start'
and 'Quit to title' do). Muse Image draws the title background and the emblem from your two
descriptions in the game's art style; they never contain text or letters."""


def menu_prompt(design: GameDesign, structure: GameStructure, context: str = "",
                screen_w: int = 320, scale: int = 1) -> str:  # fmt: skip
    return f"""Design the menus of this game as data: a MenuSpec with the theme, every screen and
its items, the credits, and what the title background and the emblem show.

{menu_rules(structure, screen_w, scale)}

Game: {design.title}. {design.pitch}
Look: {design.look}
Controls: {design.controls}
Win: {structure.win}. Lose: {structure.lose or "(cannot be lost)"}
Levels: {"; ".join(f"{lv.n}. {lv.name}: {lv.goal}" for lv in structure.levels)}
""" + (f"\nThe game's record (read-only excerpts):\n{context.strip()}\n" if context.strip() else "")


def menu_revision_prompt(spec: MenuSpec, feedback: str, design: GameDesign,
                         structure: GameStructure, screen_w: int = 320, scale: int = 1) -> str:  # fmt: skip
    return f"""Revise these menus for the human's feedback. Change only what the feedback asks
for; keep every other screen, label and colour as it is.

{menu_rules(structure, screen_w, scale)}

Game: {design.title}. Look: {design.look}

Feedback:
{feedback.strip()}

Current menus:
{spec.model_dump_json(indent=1)}"""


def offline_revision(spec: MenuSpec, feedback: str) -> MenuSpec:
    """The offline Menu Designer's revision: a colour the feedback names becomes the highlight
    and frame (the panel when it speaks of the panel or background); otherwise as it was."""
    named = look_palette(feedback)
    if not named:
        return spec
    theme = spec.theme.model_copy()
    if re.search(r"\b(panel|background|box)\b", feedback, re.IGNORECASE):
        theme.panel = named[0]
    else:
        theme.highlight = theme.border = named[0]
    return spec.model_copy(update={"theme": theme})


# --- what the SDK loads ---------------------------------------------------------------------------


def menus_script(spec: MenuSpec, structure: GameStructure, design: GameDesign,
                 credits: list[str] = (), art: dict | None = None) -> dict:  # fmt: skip
    """window.SF_MENUS (menus.js). `art`: {"title": image id, "emblem": image id} of the title
    art in the build's assets (absent: the SDK draws a plain panel)."""
    lines = [c for c in spec.credits if c.strip()]
    lines += [clip(c.split(" (")[0], 46) for c in credits if c.strip()][:6]
    return {
        "title": spec.title,
        "subtitle": spec.subtitle,
        "theme": spec.theme.model_dump(),
        "screens": {s.id: {"title": s.title, "subtitle": s.subtitle,
                           "items": [i.model_dump() for i in s.items]} for s in spec.screens},  # fmt: skip
        "levels": [lv.model_dump() for lv in structure.levels],
        "can_lose": bool(structure.lose.strip()),
        "controls": control_lines(design.controls),
        "credits": lines[:8],
        "difficulty": DIFFICULTY,
        "art": art or {},
    }


def control_lines(controls: str, width: int = 36, most: int = 6) -> list[str]:
    """The design's controls as lines that fit the panel: split at ';' and line breaks, then at
    ',' and words when a part is too long (live 2026-11-02: one long sentence was clipped)."""
    out = []
    for part in (p.strip() for p in re.split(r"[;\n]", controls) if p.strip()):
        line = ""
        for word in re.split(r"(?<=,)\s+|\s+", part):
            if line and len(line) + 1 + len(word) > width:
                out.append(line.rstrip(","))
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            out.append(line.rstrip(","))
    return out[:most]


def menus_markdown(spec: MenuSpec, notes: list[str] = (), problems: list[str] = ()) -> str:
    t = spec.theme
    screens = "\n\n".join(
        f"## {s.id}" + (f": {s.title}" if s.title else "") + (f"\n_{s.subtitle}_" if s.subtitle else "")
        + "\n" + "\n".join(f"{i}. {it.label} ({it.action})" for i, it in enumerate(s.items, 1))
        for s in spec.screens
    )  # fmt: skip
    return (
        f"# Menus: {spec.title}\n\n{spec.subtitle}\n\n"
        f"**Theme:** panel {t.panel}, frame {t.border} ({t.style}), text {t.text}, highlight "
        f"{t.highlight}, locked {t.dim}  \n**Title art:** {spec.title_art}  \n"
        f"**Emblem:** {spec.emblem}\n\n{screens}\n\n## Credits\n"
        + ("\n".join(f"- {c}" for c in spec.credits) or "- (none)")
        + ("\n\n## Repaired by code\n" + "\n".join(f"- {n}" for n in notes) if notes else "")
        + ("\n\n## Problems code could not fix\n" + "\n".join(f"- {p}" for p in problems) if problems else "")
        + "\n"
    )
