"""Sound as data (L2.7): the Sound Director picks a sound for every moment of the game, code fits
and checks it, the SDK synthesises it in the browser (`sdk/sfsound.js`, Web Audio: no audio
files, no paid tools), and the human edits any sound in the build's sound editor
(`sounds.html`) or in words at G4.

`SoundBrief` (strict; Sound Director, Muse Spark) is a list of cues: a name, the moments that
play it (`on`), what it sounds like, a preset from the library below and three tweaks (pitch,
length, volume). Code turns each into synth layers (`SoundCue`), the form the SDK plays and the
editor changes: a layer is a wave (square, triangle, sine, sawtooth or noise) whose pitch
slides from `freq` to `freq_end`, shaped by attack, hold and release, optionally filtered
(cutoff sliding to `cutoff_end`) and wobbled (vibrato on a tone, a filter sweep on noise).

Moments are what the SDK already sees (`sf.fx` moments such as jump, land, break, pickup; SDK
events such as win, lose, pause, menu_select), the design's check events, `move` (a loop that
repeats while the player moves on the ground) and the game's own events (e.g. `shoot`), which
the Programmer is told to emit. Several cues on one moment are told apart by the thing's words
(a crate's break and a wall's break), as `sf.fx` does.

`fit_brief` repairs what code can (unknown presets and moments, out-of-range tweaks, duplicate
names) and adds a cue for every moment the game has but the brief left silent; `fit_set`
clamps a set the human edited. No sound is ever generated twice by a model: an accepted set is
a locked version (`game/sounds/vS.json`), and edits make the next version.
"""

import copy
import difflib
import math
import re

from pydantic import BaseModel, Field

from spriteforge.agents.models import Contract

from .design import GameDesign, snake

WAVES = ("square", "triangle", "sine", "sawtooth", "noise")
FILTERS = ("none", "lowpass", "highpass", "bandpass")
MAX_CUES = 32
MAX_LAYERS = 4
LOOP_MOMENTS = {"move": "the player moves on the ground (repeats every `every` seconds)"}
# Moments the SDK itself plays (sf.fx in sf.js and sflevel.js; events in sf.js, sflevel.js and
# sfflow.js), with what happens at each.
FX_MOMENTS = {
    "jump": "the player jumps", "land": "the player lands", "dash": "the player dashes",
    "break": "something breaks or dissolves (a crate, a wall)", "kill": "an enemy is beaten",
    "pickup": "the player picks something up", "transform": "the player changes form",
    "hurt": "the player is hurt", "checkpoint": "a checkpoint is reached",
    "exit": "the player reaches the exit",
}  # fmt: skip
EVENT_MOMENTS = {
    "level_start": "a level starts", "respawn": "the player is sent back to a checkpoint",
    "level_complete": "a level is finished", "win": "the game is won", "lose": "the game is lost",
    "pause": "the game is paused", "resume": "the game resumes", "title": "the title screen",
    "menu_move": "the menu highlight moves", "menu_select": "a menu item is picked",
    "choice": "an in-game choice is picked",
}  # fmt: skip
SDK_MOMENTS = {**FX_MOMENTS, **EVENT_MOMENTS, **LOOP_MOMENTS}
SYNONYMS = {
    "walk": "move", "walking": "move", "moving": "move", "run": "move", "running": "move",
    "slide": "move", "sliding": "move", "step": "move", "steps": "move", "footsteps": "move",
    "footstep": "move", "crawl": "move", "jumping": "jump", "hop": "jump", "landing": "land",
    "collect": "pickup", "pick_up": "pickup", "pickups": "pickup", "coin": "pickup",
    "dashing": "dash", "smash": "break", "destroy": "break", "crumble": "break",
    "dissolve": "break", "enemy_kill": "kill", "defeat": "kill", "stomp": "kill", "damage": "hurt",
    "hit": "hurt", "transformation": "transform", "morph": "transform", "goal": "exit",
    "finish": "exit", "victory": "win", "game_over": "lose", "death": "lose", "die": "lose",
    "select": "menu_select", "confirm": "menu_select", "menu_click": "menu_select",
    "navigate": "menu_move", "menu_navigate": "menu_move", "unpause": "resume",
    "level_clear": "level_complete", "level_end": "level_complete", "start": "level_start",
}  # fmt: skip


# --- the preset library ------------------------------------------------------------------------


def L(wave: str, freq: float, freq_end: float | None = None, *, a: float = 0.005, h: float = 0.03,
      r: float = 0.1, v: float = 0.3, d: float = 0.0, filt: str = "none", cut: float = 2000,
      cut_end: float | None = None, q: float = 1.0, wob: float = 0.0, wob_depth: float = 0.0) -> dict:  # fmt: skip
    """A layer in short form (the library below)."""
    return {"wave": wave, "freq": freq, "freq_end": freq if freq_end is None else freq_end,
            "attack": a, "hold": h, "release": r, "volume": v, "delay": d, "filter": filt,
            "cutoff": cut, "cutoff_end": cut if cut_end is None else cut_end, "q": q,
            "wobble": wob, "wobble_depth": wob_depth}  # fmt: skip


# name -> (what it sounds like, layers, loop every seconds or 0)
PRESETS: dict[str, tuple[str, list[dict], float]] = {
    "splash": ("a wet splat: a noise burst and a falling blop, a droplet after it", [
        L("noise", 1, filt="bandpass", cut=1800, cut_end=450, q=1.2, h=0.04, r=0.22, v=0.55),
        L("sine", 420, 140, h=0.02, r=0.12, v=0.45),
        L("sine", 900, 1500, d=0.07, h=0.01, r=0.06, v=0.15)], 0),
    "squish": ("a rubbery boing rising up, for a soft body jumping", [
        L("sine", 220, 560, h=0.03, r=0.1, v=0.5, wob=30, wob_depth=0.08),
        L("noise", 1, filt="lowpass", cut=1200, cut_end=400, h=0.02, r=0.06, v=0.2)], 0),
    "bubble_loop": ("a low watery rumble with bubbles, repeated while moving", [
        L("noise", 1, filt="lowpass", cut=380, cut_end=260, a=0.05, h=0.2, r=0.15, v=0.35,
          wob=7, wob_depth=0.4),
        L("sine", 110, 90, a=0.03, h=0.2, r=0.1, v=0.2, wob=9, wob_depth=0.15),
        L("sine", 500, 900, d=0.12, h=0.01, r=0.05, v=0.08)], 0.3),
    "footsteps_loop": ("a soft step, repeated while walking", [
        L("noise", 1, filt="bandpass", cut=2000, cut_end=1200, q=1.5, a=0.002, h=0.01, r=0.05,
          v=0.3),
        L("sine", 150, 90, h=0.01, r=0.05, v=0.25)], 0.28),
    "jump": ("a classic chiptune jump: a square wave sliding up", [
        L("square", 280, 640, h=0.04, r=0.1, v=0.3)], 0),
    "land_thud": ("a dull thud on the ground", [
        L("noise", 1, filt="lowpass", cut=600, cut_end=120, a=0.003, h=0.02, r=0.12, v=0.5),
        L("sine", 120, 55, h=0.01, r=0.12, v=0.4)], 0),
    "whoosh": ("a fast airy whoosh, for a dash or a swing", [
        L("noise", 1, filt="bandpass", cut=500, cut_end=2600, q=1.5, a=0.04, h=0.06, r=0.14,
          v=0.45)], 0),
    "coin": ("a bright two-note coin ding", [
        L("square", 988, a=0.002, h=0.05, r=0.03, v=0.25),
        L("square", 1319, d=0.06, h=0.12, r=0.15, v=0.25)], 0),
    "sparkle": ("a glittering three-note arpeggio, for a gem or a bonus", [
        L("triangle", 1568, h=0.03, r=0.08, v=0.3),
        L("triangle", 2093, d=0.05, h=0.03, r=0.08, v=0.3),
        L("triangle", 2637, d=0.1, h=0.04, r=0.15, v=0.3)], 0),
    "powerup": ("a rising wobbly sweep, for gaining a power", [
        L("square", 300, 1200, h=0.3, r=0.1, v=0.25, wob=14, wob_depth=0.05),
        L("triangle", 600, 2400, d=0.05, h=0.25, r=0.1, v=0.15)], 0),
    "transform": ("a magical shimmering change", [
        L("sawtooth", 200, 900, a=0.02, h=0.25, r=0.15, v=0.2, filt="lowpass", cut=3000),
        L("triangle", 1200, 1800, d=0.2, h=0.05, r=0.2, v=0.15, wob=10, wob_depth=0.05)], 0),
    "laser": ("a sharp pew: a square wave diving down, for a bullet or a shot", [
        L("square", 1400, 220, a=0.001, h=0.02, r=0.1, v=0.25)], 0),
    "blaster": ("a heavier gun shot with a crack of noise", [
        L("sawtooth", 700, 90, h=0.03, r=0.16, v=0.3),
        L("noise", 1, filt="lowpass", cut=3000, cut_end=600, h=0.01, r=0.1, v=0.3)], 0),
    "explosion": ("a deep boom with a long rumble", [
        L("noise", 1, filt="lowpass", cut=1800, cut_end=60, a=0.003, h=0.1, r=0.55, v=0.7),
        L("sine", 90, 35, h=0.05, r=0.4, v=0.5)], 0),
    "crate_break": ("splintering wood: two cracks and a knock", [
        L("noise", 1, filt="bandpass", cut=900, cut_end=600, q=2, h=0.02, r=0.08, v=0.5),
        L("noise", 1, filt="bandpass", cut=1300, q=2, d=0.05, h=0.01, r=0.07, v=0.4),
        L("square", 160, 70, h=0.01, r=0.1, v=0.25)], 0),
    "wall_break": ("crumbling stone: a heavy crash and falling rubble", [
        L("noise", 1, filt="lowpass", cut=2200, cut_end=150, a=0.005, h=0.12, r=0.45, v=0.6),
        L("sine", 80, 40, h=0.05, r=0.35, v=0.45),
        L("noise", 1, filt="bandpass", cut=3000, q=2, d=0.12, h=0.01, r=0.1, v=0.2)], 0),
    "dissolve": ("a fizzing hiss, for something melting or dissolving", [
        L("noise", 1, filt="highpass", cut=2500, cut_end=6000, a=0.02, h=0.15, r=0.25, v=0.3,
          wob=18, wob_depth=0.3),
        L("sine", 300, 120, h=0.05, r=0.2, v=0.15)], 0),
    "metal_clang": ("a ringing metal clang", [
        L("square", 520, h=0.02, r=0.4, v=0.2, wob=6, wob_depth=0.01),
        L("triangle", 1310, h=0.02, r=0.3, v=0.2),
        L("noise", 1, filt="highpass", cut=4000, h=0.01, r=0.05, v=0.2)], 0),
    "hurt": ("a buzzy falling ouch", [
        L("square", 420, 160, h=0.05, r=0.12, v=0.3, wob=30, wob_depth=0.2)], 0),
    "enemy_pop": ("a squelchy pop, for an enemy beaten", [
        L("noise", 1, filt="bandpass", cut=1500, cut_end=300, q=1.5, h=0.02, r=0.12, v=0.45),
        L("square", 700, 120, h=0.02, r=0.12, v=0.25)], 0),
    "chime": ("a friendly three-note chime, for a checkpoint", [
        L("triangle", 523, h=0.05, r=0.2, v=0.25),
        L("triangle", 659, d=0.08, h=0.05, r=0.2, v=0.25),
        L("triangle", 784, d=0.16, h=0.08, r=0.3, v=0.25)], 0),
    "door": ("a soft rising tone, for an exit or a door", [
        L("triangle", 392, 784, a=0.02, h=0.2, r=0.2, v=0.25)], 0),
    "fanfare": ("a short victory fanfare", [
        L("square", 523, h=0.08, r=0.04, v=0.2), L("square", 659, d=0.1, h=0.08, r=0.04, v=0.2),
        L("square", 784, d=0.2, h=0.08, r=0.04, v=0.2),
        L("square", 1047, d=0.3, h=0.25, r=0.3, v=0.22)], 0),
    "sad": ("a falling three-note lament, for losing", [
        L("triangle", 392, h=0.12, r=0.1, v=0.3), L("triangle", 330, d=0.18, h=0.12, r=0.1, v=0.3),
        L("triangle", 262, 247, d=0.36, h=0.25, r=0.35, v=0.3)], 0),
    "respawn": ("a quick rising pop-in", [
        L("sine", 300, 900, h=0.1, r=0.1, v=0.3)], 0),
    "blip": ("a tiny blip, for moving through a menu", [
        L("square", 660, a=0.002, h=0.02, r=0.02, v=0.15)], 0),
    "select": ("a two-note confirm, for picking a menu item", [
        L("square", 660, a=0.002, h=0.03, r=0.02, v=0.2),
        L("square", 990, d=0.05, h=0.04, r=0.05, v=0.2)], 0),
    "pause": ("two soft notes, for pause and resume", [
        L("triangle", 880, h=0.02, r=0.06, v=0.2), L("triangle", 660, d=0.07, h=0.02, r=0.08, v=0.2)], 0),
}  # fmt: skip
WORDS_PRESET = [  # words in a cue (its name, what it sounds like) -> the preset that fits best
    (r"splash|splat|squelch|slosh|wet", "splash"), (r"boing|squish|bounce|spring", "squish"),
    (r"bubbl|gurgl|rumbl|water|ooze|slime.*(move|loop)", "bubble_loop"),
    (r"step|walk|footstep", "footsteps_loop"), (r"whoosh|swish|swoosh|dash|swing", "whoosh"),
    (r"laser|pew|zap|bullet|shot|shoot|fire", "laser"), (r"blast|gun|cannon", "blaster"),
    (r"boom|explo|bomb", "explosion"), (r"wood|crate|box|splinter|barrel", "crate_break"),
    (r"stone|rock|wall|rubble|crumbl|brick", "wall_break"), (r"fizz|hiss|melt|dissolv|acid", "dissolve"),
    (r"metal|clang|clank|anvil|steel|iron", "metal_clang"), (r"coin|ding|cash", "coin"),
    (r"gem|sparkl|glitter|star|crystal", "sparkle"), (r"power|upgrade|level.?up", "powerup"),
    (r"transform|morph|magic|shimmer", "transform"), (r"hurt|ouch|damage|hit", "hurt"),
    (r"pop|enemy|defeat|kill|stomp", "enemy_pop"), (r"chime|checkpoint|bell|flag", "chime"),
    (r"door|exit|portal|goal", "door"), (r"fanfare|victory|win|complete|clear", "fanfare"),
    (r"sad|lose|game.?over|death|fail", "sad"), (r"respawn|appear|spawn", "respawn"),
    (r"pause|resume", "pause"), (r"select|confirm|click|choose", "select"),
    (r"blip|menu|cursor|tick", "blip"), (r"thud|land|thump", "land_thud"), (r"jump|hop", "jump"),
]  # fmt: skip
BLOB = re.compile(r"slime|blob|jelly|ooze|goo\b|gel\b|pudding|water|liquid", re.IGNORECASE)
WOOD = re.compile(r"wood|crate|box|barrel|plank", re.IGNORECASE)
STONE = re.compile(r"stone|rock|wall|brick|crack", re.IGNORECASE)
METAL = re.compile(r"metal|iron|steel", re.IGNORECASE)
ACID = re.compile(r"acid|melt|dissolv|corro", re.IGNORECASE)


def preset_for(text: str, fallback: str = "blip") -> str:
    """The preset whose words appear in `text` (a cue's name and what it sounds like)."""
    return next((p for pattern, p in WORDS_PRESET if re.search(pattern, text, re.IGNORECASE)), fallback)


# --- schemas -----------------------------------------------------------------------------------


class CueIdea(Contract):
    name: str = Field(description="snake_case name, e.g. 'slime_splash_land', 'crate_crunch'")
    on: list[str] = Field(description="the moments that play it, from the list given (e.g. "
                          "['land']); a game event of its own only when no listed moment fits")
    what: str = Field(description="what it sounds like and why it fits, in one sentence")
    preset: str = Field(description="the library preset it starts from")
    pitch: float = Field(description="pitch multiplier, 0.5 (an octave lower) to 2 (higher); 1 as is")
    length: float = Field(description="length multiplier, 0.5 to 2; 1 as is")
    volume: float = Field(description="0.1 (quiet) to 1 (full)")


class SoundBrief(Contract):
    style: str = Field(description="the sound direction in one sentence (e.g. 'wet, bubbly chiptune')")
    cues: list[CueIdea]


class SoundLayer(BaseModel):
    """One synth voice of a cue (what the editor changes)."""

    wave: str = "square"
    freq: float = 440.0  # Hz at the start (ignored for noise)
    freq_end: float = 440.0  # Hz at the end
    attack: float = 0.005  # seconds
    hold: float = 0.03
    release: float = 0.1
    volume: float = 0.3  # 0-1
    delay: float = 0.0  # seconds after the cue starts
    filter: str = "none"
    cutoff: float = 2000.0  # Hz at the start
    cutoff_end: float = 2000.0
    q: float = 1.0
    wobble: float = 0.0  # Hz: vibrato on a tone, a filter sweep (or tremolo) on noise
    wobble_depth: float = 0.0  # 0-1 of the pitch or cutoff

    def seconds(self) -> float:
        return self.delay + self.attack + self.hold + self.release


class SoundCue(BaseModel):
    name: str
    on: list[str]
    what: str = ""
    preset: str = ""
    layers: list[SoundLayer]
    volume: float = 1.0
    loop: bool = False  # repeats while its moment holds (move)
    every: float = 0.3  # seconds between repeats of a loop
    jitter: float = 0.04  # random pitch change per play, so repeats do not sound identical
    match: list[str] = Field(default_factory=list)  # words that pick it among cues of one moment

    def seconds(self) -> float:
        return max((x.seconds() for x in self.layers), default=0.0)


class SoundSet(BaseModel):
    style: str = ""
    volume: float = 0.8  # master
    cues: list[SoundCue]


class SoundRecord(BaseModel):
    """What the run keeps (`game/sounds/vS.json`)."""

    sounds: SoundSet
    brief: SoundBrief | None = None  # the Sound Director's answer (None: edited by the human)
    by: str = ""
    why: str = ""
    notes: list[str] = Field(default_factory=list)  # what code repaired or added
    problems: list[str] = Field(default_factory=list)  # what code could not repair
    custom: dict[str, str] = Field(default_factory=dict)  # events the game's code must emit
    retried: bool = False
    build: int | None = None  # the build the human edited it on


# --- turning ideas into layers, clamping -------------------------------------------------------

RANGES = {
    "freq": (20.0, 12000.0), "freq_end": (20.0, 12000.0), "attack": (0.0, 1.0),
    "hold": (0.0, 2.0), "release": (0.005, 2.0), "volume": (0.0, 1.0), "delay": (0.0, 2.0),
    "cutoff": (40.0, 16000.0), "cutoff_end": (40.0, 16000.0), "q": (0.1, 20.0),
    "wobble": (0.0, 60.0), "wobble_depth": (0.0, 1.0),
}  # fmt: skip


def clamp(x: float, lo: float, hi: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return lo
    return lo if math.isnan(x) else max(lo, min(hi, x))


def fit_layer(layer: SoundLayer) -> SoundLayer:
    data = layer.model_dump()
    for k, (lo, hi) in RANGES.items():
        data[k] = round(clamp(data[k], lo, hi), 4)
    data["wave"] = data["wave"] if data["wave"] in WAVES else "square"
    data["filter"] = data["filter"] if data["filter"] in FILTERS else "none"
    return SoundLayer(**data)


def preset_layers(preset: str, pitch: float = 1.0, length: float = 1.0) -> list[SoundLayer]:
    """The preset's layers, pitched (frequencies and cutoffs) and stretched (times)."""
    out = []
    for raw in copy.deepcopy(PRESETS[preset][1]):
        for k in ("freq", "freq_end", "cutoff", "cutoff_end"):
            raw[k] *= pitch
        for k in ("attack", "hold", "release", "delay"):
            raw[k] *= length
        out.append(fit_layer(SoundLayer(**raw)))
    return out


def make_cue(name: str, on: list[str], preset: str, what: str = "", pitch: float = 1.0,
             length: float = 1.0, volume: float = 1.0, match: list[str] = ()) -> SoundCue:  # fmt: skip
    every = PRESETS[preset][2]
    loop = any(m in LOOP_MOMENTS for m in on)
    return SoundCue(name=name, on=on, what=what or PRESETS[preset][0], preset=preset,
                    layers=preset_layers(preset, pitch, length), volume=round(volume, 3),
                    loop=loop, every=round(clamp((every or 0.3) * length, 0.08, 2.0), 3),
                    match=list(match))  # fmt: skip


def fit_cue(cue: SoundCue) -> SoundCue:
    layers = [fit_layer(x) for x in cue.layers[:MAX_LAYERS]] or preset_layers("blip")
    on = list(dict.fromkeys(m for m in (snake(x) for x in cue.on) if m != "x")) or ["menu_select"]
    loop = any(m in LOOP_MOMENTS for m in on)
    if loop:
        on = [m for m in on if m in LOOP_MOMENTS]
    return cue.model_copy(update={
        "name": snake(cue.name)[:40], "on": on, "layers": layers, "loop": loop,
        "volume": round(clamp(cue.volume, 0.0, 1.0), 3), "every": round(clamp(cue.every, 0.08, 2.0), 3),
        "jitter": round(clamp(cue.jitter, 0.0, 0.3), 3),
        "match": [snake(w) for w in cue.match if snake(w) != "x"][:8], "what": cue.what[:200],
    })  # fmt: skip


def unique(name: str, taken: set[str]) -> str:
    out, i = name, 2
    while out in taken:
        out, i = f"{name}_{i}", i + 1
    taken.add(out)
    return out


def fit_set(sounds: SoundSet) -> tuple[SoundSet, list[str]]:
    """Clamp a set (the human's edits in the sound editor): names unique, values in range."""
    notes, taken, cues = [], set(), []
    for cue in sounds.cues[:MAX_CUES]:
        fitted = fit_cue(cue)
        name = unique(fitted.name, taken)
        if fitted.model_dump() != cue.model_dump() or name != fitted.name:
            notes.append(f"{cue.name or '(unnamed)'}: values brought into range")
        cues.append(fitted.model_copy(update={"name": name}))
    if len(sounds.cues) > MAX_CUES:
        notes.append(f"only the first {MAX_CUES} sounds are kept")
    return SoundSet(style=sounds.style[:200], volume=round(clamp(sounds.volume, 0.0, 1.0), 3),
                    cues=cues), notes  # fmt: skip


# --- the moments of one game -------------------------------------------------------------------


def blob_player(design: GameDesign) -> bool:
    player = next((e for e in design.entities if e.role == "player"), None)
    return bool(player and BLOB.search(f"{player.id} {player.look}"))


def design_text(design: GameDesign) -> str:
    return " ".join([design.pitch, *design.rules, design.player, design.controls, design.world,
                     *(f"{e.id} {e.look} {e.behaviour}" for e in design.entities)])  # fmt: skip


def moments_of(design: GameDesign, levels: bool, menus: bool) -> dict[str, str]:
    """Every moment this game has, with what happens: the SDK's (by what the game has), the
    design's check events."""
    text, roles = design_text(design).lower(), {e.role for e in design.entities}
    out = {"move": LOOP_MOMENTS["move"]}
    wanted = {"jump": levels or "jump" in text, "land": levels or "jump" in text,
              "dash": levels or "dash" in text, "break": bool(re.search(r"break|crate|wall|dissolv|smash", text)),
              "kill": "enemy" in roles, "pickup": bool(roles & {"pickup", "item", "collectible"}) or
              bool(re.search(r"coin|gem|pick|collect|power", text)),
              "transform": bool(re.search(r"transform|form\b|morph", text)), "hurt": True,
              "checkpoint": levels, "exit": levels}  # fmt: skip
    out |= {m: FX_MOMENTS[m] for m, yes in wanted.items() if yes}
    out |= {"respawn": EVENT_MOMENTS["respawn"]} if levels else {}
    out |= {m: EVENT_MOMENTS[m] for m in ("level_complete", "win", "lose")}
    if menus:
        out |= {m: EVENT_MOMENTS[m] for m in ("menu_move", "menu_select", "pause", "resume")}
    for c in design.checks:
        out.setdefault(snake(c.event), c.description)
    return out


# A design event the SDK already sounds as one of its moments ("crate_open" is a break): it
# shares that moment's cue, so one thing never plays two sounds at once.
EVENT_FX = [
    ("kill", r"kill|defeat|stomp|enemy|beat"), ("break", r"break|open|dissolv|smash|destroy|crumbl|wall"),
    ("pickup", r"collect|pick|coin|gem|power|item|take"), ("hurt", r"hurt|damage|hit"),
    ("transform", r"transform|form\b|morph"), ("jump", r"jump"), ("land", r"land"), ("dash", r"dash"),
    ("checkpoint", r"checkpoint"), ("exit", r"exit|door|goal"),
]  # fmt: skip


def sdk_moment_of(event: str, moments: dict[str, str]) -> str | None:
    """The SDK moment a design event happens at, when this game has that moment."""
    if event in SDK_MOMENTS:
        return None
    text = event.replace("_", " ")
    return next((m for m, pattern in EVENT_FX if m in moments and re.search(pattern, text)), None)


def default_preset(moment: str, design: GameDesign) -> str:
    blob = blob_player(design)
    table = {
        "move": "bubble_loop" if blob else "footsteps_loop", "jump": "squish" if blob else "jump",
        "land": "splash" if blob else "land_thud", "dash": "whoosh", "break": "crate_break",
        "kill": "enemy_pop", "pickup": "coin", "transform": "transform", "hurt": "hurt",
        "checkpoint": "chime", "exit": "door", "respawn": "respawn", "level_complete": "fanfare",
        "win": "fanfare", "lose": "sad", "menu_move": "blip", "menu_select": "select",
        "pause": "pause", "resume": "pause", "title": "chime", "level_start": "door",
        "choice": "select",
    }  # fmt: skip
    return table.get(moment) or preset_for(moment.replace("_", " "), "coin")


def breakables(design: GameDesign) -> list[tuple[str, str]]:
    """(entity id, preset) for every thing that breaks, by its material."""
    out = []
    for e in design.entities:
        text = f"{e.id} {e.look} {e.behaviour}"
        if not re.search(r"break|crack|crate|dissolv|smash|destroy|crumbl", text, re.IGNORECASE):
            continue
        if e.role in ("player", "enemy", "pickup", "item", "collectible", "background"):
            continue  # a coin "from crates" is collected, not broken (live 2026-11-03)
        # What it is made of, by its own name first: a crate holding metal is still a crate.
        preset = ("crate_break" if WOOD.search(e.id) else "wall_break" if STONE.search(e.id)
                  else "metal_clang" if METAL.search(e.id) else "crate_break" if WOOD.search(text)
                  else "wall_break" if STONE.search(text) else "crate_break")  # fmt: skip
        out.append((e.id, preset))
    return out


def fallback_brief(design: GameDesign, levels: bool = False, menus: bool = False) -> SoundBrief:
    """The offline Sound Director: a cue per moment from the material of what makes it (a slime
    splashes, a crate splinters, a wall crumbles), one per breakable thing."""
    cues, blob = [], blob_player(design)
    moments = moments_of(design, levels, menus)
    for moment in moments:
        if sdk_moment_of(moment, moments):
            continue  # the SDK's moment's cue plays it (see `alias`)
        if moment == "break" and (things := breakables(design)):
            for eid, preset in things:
                cues.append(CueIdea(name=f"{eid}_{preset.split('_')[0]}", on=["break"],
                                    what=f"{eid} breaking: {PRESETS[preset][0]}", preset=preset,
                                    pitch=1.0, length=1.0, volume=0.9))  # fmt: skip
            continue
        preset = default_preset(moment, design)
        if moment == "transform" and ACID.search(design_text(design)):
            preset = "transform"
        name = {"move": "slime_bubble_move" if blob else "footsteps",
                "land": "slime_splash_land" if blob else "land_thud",
                "jump": "slime_squish_jump" if blob else "jump"}.get(moment, moment)  # fmt: skip
        cues.append(CueIdea(name=name, on=[moment], what=PRESETS[preset][0], preset=preset,
                            pitch=1.0, length=1.0, volume=0.6 if moment == "move" else 0.9))  # fmt: skip
    style = "wet, bubbly chiptune" if blob else "bright chiptune"
    return SoundBrief(style=style, cues=cues)


def moment_name(text: str, known: set[str]) -> str:
    m = snake(text)
    if m in known:
        return m
    if m in SYNONYMS:
        return SYNONYMS[m]
    close = difflib.get_close_matches(m, sorted(known), n=1, cutoff=0.85)
    return close[0] if close else m


def match_words(cue: CueIdea, design: GameDesign) -> list[str]:
    """The entity ids a cue names (its name or what it sounds like), to pick it for that thing."""
    text = f"{cue.name} {cue.what}".lower().replace("_", " ")
    out = []
    for e in design.entities:
        words = [w for w in e.id.split("_") if len(w) > 2]
        if words and any(re.search(rf"\b{re.escape(w)}", text) for w in words):
            out.append(e.id)
    return out


def fit_brief(brief: SoundBrief, design: GameDesign, levels: bool = False,
              menus: bool = False) -> tuple[SoundSet, list[str], dict[str, str]]:  # fmt: skip
    """The brief as a sound set: presets and moments repaired, tweaks clamped, a cue added for
    every moment the brief left silent. Returns (set, notes, custom events the code emits)."""
    moments = moments_of(design, levels, menus)
    known = set(SDK_MOMENTS) | set(moments)
    notes, taken, cues, custom = [], set(), [], {}
    for idea in brief.cues[:MAX_CUES]:
        preset = idea.preset if idea.preset in PRESETS else None
        if preset is None:
            close = difflib.get_close_matches(snake(idea.preset), list(PRESETS), n=1, cutoff=0.7)
            preset = close[0] if close else preset_for(f"{idea.preset} {idea.name} {idea.what}")
            notes.append(f"{idea.name}: preset '{idea.preset}' is not in the library: {preset}")
        on = []
        for raw in idea.on:
            m = moment_name(raw, known)
            if m != snake(raw):
                notes.append(f"{idea.name}: moment '{raw}' is '{m}'")
            if m not in known:
                custom[m] = idea.what[:120]
            on.append(m)
        on = list(dict.fromkeys(on))
        if not on:
            notes.append(f"{idea.name}: plays on no moment: dropped")
            continue
        pitch, length, volume = (clamp(idea.pitch, 0.5, 2.0), clamp(idea.length, 0.5, 2.0),
                                 clamp(idea.volume, 0.1, 1.0))  # fmt: skip
        if (pitch, length, volume) != (idea.pitch, idea.length, idea.volume):
            notes.append(f"{idea.name}: pitch, length or volume brought into range")
        name = unique(snake(idea.name)[:40], taken)
        cue = make_cue(name, on, preset, idea.what[:200], pitch, length, volume, match_words(idea, design))
        cues.append(fit_cue(cue).model_copy(update={"name": name}))
    covered = {m for c in cues for m in c.on}
    defaults = fallback_brief(design, levels, menus)
    for idea in defaults.cues:
        if idea.on[0] in covered or (idea.on[0] == "break" and "break" in covered):
            continue
        name = unique(idea.name, taken)
        cues.append(make_cue(name, idea.on, idea.preset, idea.what, volume=idea.volume,
                             match=match_words(idea, design)))  # fmt: skip
        notes.append(f"'{idea.on[0]}' had no sound: code added {name} ({idea.preset})")
    cues = alias(cues, moments, notes)
    for m in custom:
        notes.append(f"'{m}' is the game's own event: the Programmer emits sf.event('{m}')")
    return SoundSet(style=brief.style[:200] or defaults.style, cues=cues[:MAX_CUES]), notes, custom


def alias(cues: list[SoundCue], moments: dict[str, str], notes: list[str]) -> list[SoundCue]:
    """Each design event no cue plays and the SDK sounds as one of its moments joins that
    moment's cue (the one named after the thing the event names, else the first): `crate_open`
    plays the crate's break sound, once (a cue plays at most once a step)."""
    playing = {m for c in cues for m in c.on}
    out = list(cues)
    for event in moments:
        sdk = sdk_moment_of(event, moments)
        if event in playing or not sdk:
            continue
        words = set(event.split("_"))
        same = [i for i, c in enumerate(out) if sdk in c.on and not c.loop]
        if not same:
            continue
        pick = next((i for i in same if words & (set(out[i].name.split("_"))
                                                 | {w for m in out[i].match for w in m.split("_")})),
                    same[0])  # fmt: skip
        out[pick] = out[pick].model_copy(update={"on": [*out[pick].on, event]})
        notes.append(f"'{event}' happens at '{sdk}': it plays {out[pick].name}")
    return out


def sound_problems(sounds: SoundSet) -> list[str]:
    """What code cannot repair: a cue nobody can hear, several cues on one moment with nothing
    to tell them apart."""
    out = []
    for c in sounds.cues:
        if c.volume <= 0 or all(x.volume <= 0 for x in c.layers):
            out.append(f"{c.name} is silent (volume 0)")
        elif c.seconds() > 3:
            out.append(f"{c.name} lasts {c.seconds():.1f} s (a sound effect should be under 3 s)")
    by_moment: dict[str, list[SoundCue]] = {}
    for c in sounds.cues:
        for m in c.on:
            by_moment.setdefault(m, []).append(c)
    for m, cs in by_moment.items():
        if len(cs) > 1 and sum(not (c.match or len(c.name.split("_")) > 1) for c in cs) > 1:
            out.append(f"{len(cs)} sounds play on '{m}' with nothing to tell them apart: "
                       "name each after the thing it is for (e.g. crate_crunch, wall_crumble)")  # fmt: skip
    if sounds.volume <= 0:
        out.append("the master volume is 0")
    return out


def make_record(brief: SoundBrief, design: GameDesign, by: str, levels: bool = False,
                menus: bool = False, why: str = "", retried: bool = False) -> SoundRecord:  # fmt: skip
    sounds, notes, custom = fit_brief(brief, design, levels, menus)
    return SoundRecord(sounds=sounds, brief=brief, by=by, why=why, notes=notes,
                       problems=sound_problems(sounds), custom=custom, retried=retried)  # fmt: skip


def edited_record(sounds: SoundSet, old: SoundRecord, build: int | None,
                  known: set[str]) -> SoundRecord:  # fmt: skip
    """The human's edits (sound editor) as a new record. `known`: the moments the game emits
    (the SDK's and the design's, plus the game's own events of `old`); a cue on any other moment
    stays silent until the game's code emits it, and the notes say so."""
    fitted, notes = fit_set(sounds)
    used = {m for c in fitted.cues for m in c.on}
    custom = {m: w for m, w in old.custom.items() if m in used}
    for m in sorted(used - known - set(custom)):
        what = next((c.what for c in fitted.cues if m in c.on), "")
        custom[m] = what[:120]
        notes.append(f"'{m}' is not a moment the game emits yet: the sound plays once the game's "
                     f"code emits sf.event('{m}') (describe it at the playtest gate)")  # fmt: skip
    why = "edited in the sound editor" + (f" on build v{build}" if build else "")
    return SoundRecord(sounds=fitted, brief=None, by="human (sound editor)", why=why, notes=notes,
                       problems=sound_problems(fitted), custom=custom, build=build)  # fmt: skip


def refit(record: SoundRecord, design: GameDesign, levels: bool, menus: bool) -> SoundRecord:
    """A record checked again for a changed design (no model call): moments the new design has
    and no cue plays get one; every cue kept keeps its layers. The same record when nothing
    changes."""
    again = keep_edits(record, make_record(as_brief(record), design, record.by, levels, menus,
                                           record.why))  # fmt: skip
    shape = lambda r: [(c.name, c.on) for c in r.sounds.cues]
    if shape(again) == shape(record):
        return record
    added = [c.name for c in again.sounds.cues if c.name not in {x.name for x in record.sounds.cues}]
    return again.model_copy(update={
        "brief": record.brief, "custom": {**record.custom, **again.custom},
        "why": "refitted to the changed design" + (f": added {', '.join(added)}" if added else ""),
        "build": None})  # fmt: skip


# --- prompts -----------------------------------------------------------------------------------


def library_text() -> str:
    return "\n".join(f"* {name}: {what}" + (" (a loop)" if every else "")
                     for name, (what, _, every) in PRESETS.items())  # fmt: skip


def sound_prompt(design: GameDesign, levels: bool, menus: bool, feel: str = "",
                 context: str = "") -> str:  # fmt: skip
    moments = moments_of(design, levels, menus)
    lines = "\n".join(f"* {m}: {what}" for m, what in moments.items())
    things = ", ".join(f"{e.id} ({e.role})" for e in design.entities)
    return (
        "Design the sound effects of this game as data. The SDK synthesises every sound in the "
        "browser from a library preset (chiptune voices and filtered noise), so write no code and "
        "name no audio files. For each cue give its moments (`on`), what it sounds like, a preset "
        "and pitch, length and volume tweaks.\n\n"
        "Rules:\n"
        "* Make each sound fit the thing that makes it: a slime splashes when it lands, squishes "
        "when it jumps and bubbles while it moves; wood splinters, stone crumbles, metal clangs, "
        "a shot goes pew; a coin dings.\n"
        "* Use the moments listed below (the SDK and the design already emit them). `move` is a "
        "loop that repeats while the player moves on the ground: pick a loop preset for it.\n"
        "* When several things share a moment (a crate and a wall both on `break`), give each its "
        "own cue named after the thing (e.g. wooden_crate_crunch, cracked_wall_crumble).\n"
        "* Add a moment of your own (e.g. `shoot` when the player fires) only for something that "
        "happens in the game and has no listed moment; the Programmer will emit it.\n"
        "* Keep it pleasant: background loops quieter (volume 0.3-0.6), menu sounds short and "
        "soft, nothing longer than two seconds.\n\n"
        f"Moments of this game:\n{lines}\n\nPreset library:\n{library_text()}\n\n"
        f"Game: {design.title}. {design.pitch}\nLook: {design.look}\nThings: {things}\n"
        + (f"\nWhat the game plan says players hear:\n{feel}\n" if feel else "")
        + (f"\nThe game's record:\n{context}\n" if context else "")
    )


def retry_prompt(prompt: str, problems: list[str]) -> str:
    return prompt + "\n\nCode found these problems in your last answer; fix them:\n" + "\n".join(
        f"* {p}" for p in problems)


def revision_prompt(record: SoundRecord, feedback: str, design: GameDesign, levels: bool,
                    menus: bool) -> str:  # fmt: skip
    current = "\n".join(
        f"* {c.name} on {', '.join(c.on)}: {c.what} (preset {c.preset}, volume {c.volume})"
        for c in record.sounds.cues)  # fmt: skip
    return (
        "A human played the game and wants its sounds changed. Write the whole sound brief "
        "again: change only what their words name, keep every other cue as it is (same name, "
        "preset and tweaks; pitch 1 and length 1 keep a cue as it was).\n\n"
        f"Their words:\n{feedback.strip()}\n\nCurrent sounds:\n{current}\n\n"
        + sound_prompt(design, levels, menus)
    )


QUIET = re.compile(r"quiet|softer|too loud|lower the volume|less loud|turn down", re.IGNORECASE)
LOUD = re.compile(r"louder|too quiet|can'?t hear|turn up|more volume", re.IGNORECASE)
MUTE = re.compile(r"\b(no|remove|mute|without|drop|silence)\b", re.IGNORECASE)
HIGH = re.compile(r"higher|squeakier|brighter", re.IGNORECASE)
LOW = re.compile(r"lower pitch|deeper|lower-pitched|bassier", re.IGNORECASE)


def as_brief(record: SoundRecord) -> SoundBrief:
    """A record's cues as ideas with neutral tweaks (pitch 1, length 1): `keep_edits` then keeps
    each cue's own layers (shaped by a preset, a revision or the human)."""
    return SoundBrief(style=record.sounds.style, cues=[
        CueIdea(name=c.name, on=c.on, what=c.what, preset=c.preset or preset_for(c.name),
                pitch=1.0, length=1.0, volume=c.volume) for c in record.sounds.cues])  # fmt: skip


def named(cue: SoundCue, text: str) -> bool:
    words = {w for w in cue.name.split("_") if len(w) > 2} | set(cue.on) | set(cue.match)
    return any(re.search(rf"\b{re.escape(w.replace('_', ' '))}", text) for w in words)


def offline_revision(record: SoundRecord, feedback: str) -> SoundBrief:
    """The offline Sound Director's revision: cues the words name get quieter, louder, higher,
    lower or removed; with no cue named, the whole set does."""
    text = feedback.lower().replace("_", " ")
    brief = as_brief(record)
    hit = [c for c in record.sounds.cues if named(c, text)]
    targets = {c.name for c in hit} or {c.name for c in record.sounds.cues}
    out = []
    for idea in brief.cues:
        if idea.name in targets:
            if MUTE.search(text) and hit:
                continue
            vol = idea.volume * (0.5 if QUIET.search(text) else 1.5 if LOUD.search(text) else 1)
            pitch = 1.5 if HIGH.search(text) else 0.7 if LOW.search(text) else 1.0
            idea = idea.model_copy(update={"volume": round(min(1.0, vol), 3), "pitch": pitch})
        out.append(idea)
    return brief.model_copy(update={"cues": out})


def keep_edits(old: SoundRecord, new: SoundRecord) -> SoundRecord:
    """A revision keeps the layers of every cue it left as it was (the human's edits in the
    editor survive a revision of other cues)."""
    before = {c.name: c for c in old.sounds.cues}
    ideas = {i.name: i for i in (new.brief.cues if new.brief else [])}
    cues = []
    for c in new.sounds.cues:
        was, idea = before.get(c.name), ideas.get(c.name)
        same = idea and idea.preset == (was.preset or preset_for(was.name)) if was else False
        if same and idea.pitch == 1 and idea.length == 1:
            c = was.model_copy(update={"on": c.on, "volume": c.volume, "what": c.what})
        cues.append(c)
    return new.model_copy(update={"sounds": new.sounds.model_copy(update={"cues": cues})})


# --- what the build loads ----------------------------------------------------------------------


def sounds_script(record: SoundRecord, title: str = "") -> dict:
    """`window.SF_SOUNDS`: the cues the SDK plays, the library and moments the editor offers."""
    return {
        "title": title,
        "style": record.sounds.style,
        "volume": record.sounds.volume,
        "cues": [c.model_dump() for c in record.sounds.cues],
        "custom": record.custom,
        "moments": {**SDK_MOMENTS, **record.custom},
        "presets": {name: {"what": what, "layers": layers, "every": every}
                    for name, (what, layers, every) in PRESETS.items()},
    }


def sound_events(record: SoundRecord | None) -> dict[str, str]:
    """Events only the game's code knows (e.g. shoot): the Programmer emits them."""
    return dict(record.custom) if record else {}


def sounds_markdown(record: SoundRecord) -> str:
    s = record.sounds
    lines = [f"# Sounds ({record.by})", "", f"Style: {s.style or '-'}; master volume {s.volume:g}.",
             f"Why: {record.why}" if record.why else "", "", "| sound | plays on | preset | what |",
             "| --- | --- | --- | --- |"]  # fmt: skip
    for c in s.cues:
        loop = f" (loop every {c.every:g} s)" if c.loop else ""
        lines.append(f"| {c.name} | {', '.join(c.on)}{loop} | {c.preset or 'custom'} | {c.what} |")
    if record.custom:
        lines += ["", "Events the game's code emits for these sounds:"]
        lines += [f"* `{m}`: {w}" for m, w in record.custom.items()]
    if record.notes:
        lines += ["", "Code repaired or added:"] + [f"* {n}" for n in record.notes]
    if record.problems:
        lines += ["", "Problems code could not fix:"] + [f"* {p}" for p in record.problems]
    lines += ["", ("Edit any sound in the build's sound editor (sounds.html) or describe the change "
                   "at the playtest gate.")]
    return "\n".join(x for x in lines if x is not None) + "\n"


def sound_report(record: SoundRecord, version: int, heard: dict) -> dict:
    """report.json "sounds": the set the build plays and what the bot's playtest heard."""
    played = {c.name: int(heard.get(c.name, 0)) for c in record.sounds.cues}
    return {
        "version": version,
        "by": record.by,
        "cues": [{"name": c.name, "on": c.on, "preset": c.preset, "loop": c.loop,
                  "heard": played[c.name]} for c in record.sounds.cues],
        "never_heard": [n for n, k in played.items() if not k],
        "custom": record.custom,
        "problems": record.problems,
    }
